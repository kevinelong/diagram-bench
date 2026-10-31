"""group_prompts.py - put pictures into virtual folders by prompt similarity, without touching the disk.

What this is for: a folder of 1,700 renders is a list of timestamps, not a library. The pictures that belong together -
the same scene re-worded, re-seeded, re-styled, re-sampled at another strength - are scattered through it. This module
derives the groups from the prompt text alone, so it starts no model, writes nothing, and can run in the background
while the window stays usable.

How it decides, and why this way (measured 2026-10-27):

  * the first attempt used token overlap on raw prompts and over-merged: these prompts run to ~70 tokens of shared
    scaffolding, so their nearest-neighbour similarity has a median of **0.96** and any threshold lumps unrelated scenes
    together (144 images in one group at 0.6).
  * down-weighting tokens that are frequent *in this corpus* (idf) fixes that. Against a synthetic set of six scenes x
    six phrasings - two of the six produced by this app's own rephrase walk, so the synonyms are real - idf cosine
    separates them **perfectly (P 1.00, R 1.00) at threshold 0.10-0.20**, while a rare-tokens-only representation
    scores **0.00**. So: all tokens, idf-weighted, cosine.
  * a low threshold is the safe end: precision stayed 1.00 down to 0.10 on that set, and a group that is too small is a
    nuisance while a group that mixes scenes is a lie.

The output is a list of blocks for a UI: the newest N images as they are (no folder), then one block per group, oldest
group first. Nothing here reads or writes the gallery; the caller decides what to do with the blocks.
"""
import math
import re
from collections import Counter
from pathlib import Path

DEFAULT_THRESHOLD = 0.20
# Roots are whole prompts, so two roots that look alike are usually two runs of one scene; pictures drift further apart
# than that when they are edited. Measured: at the picture threshold one family swallowed 69 series, which is a folder
# nobody can read, so the roots are held to a tighter standard of "the same scene".
FAMILY_THRESHOLD = 0.10
# The share of the shorter root's words that must appear in the longer one for the two to be one family. Measured on the
# gallery: one character written two ways sits at 0.88-0.90 containment while unrelated prompts fall far below it.
# How alike two roots must be to be one family. Measured on the real gallery: 0.50 merges wordings of one scene (as low
# as 0.53 for two descriptions of six women by a pool) without chaining unrelated roots together; 0.55 leaves that pair
# apart and 0.45 pulls the two largest families into one.
ROOT_LINK = 0.50
# Cosine alone is not enough: a three-word prompt ("a woman not a lady") sharing one word with a long character prompt
# scores 0.46, because normalisation hides how little it says. An absolute idf floor cannot fix that - idf shrinks with
# the corpus, so five identical prompts in a five-document lexicon share almost no weight - but *coverage* can: how
# much of the shorter prompt's own content the longer one accounts for. Measured on the 42-prompt benchmark, in-scene
# phrasings cover 0.76-1.00 while every cross-scene pair covers at most 0.16; the space-pirate impostor (sharing only
# "lady" with a two-word prompt of its own) sits at 0.50. Hence 0.65: scale-free, and it still keeps rephrasings.
MIN_SHARED_COVERAGE = 0.65
# ...unless the match is near-identical on the synonym-aware cosine, which is how rephrased prompts stay together: the
# dictionary substitutes words, so direct token overlap collapses even though the two prompts say the same thing.
STRONG_MATCH = 0.85
# The decisive test, and the scale-free one: a folder is judged by its most specific member, and a member must be
# comparably specific to sit in it. "a woman not a lady" is two content words against a fourteen-word character
# prompt - it scored 0.46 on cosine purely by sharing "lady", and no cosine threshold can separate that from a genuine
# rephrase (which lands around 0.35-0.45). Length can: a rephrase keeps the length, a fragment does not.
MIN_LENGTH_RATIO = 0.4
# ...and the core must account for at least this much of the member's own content. Synonym links count here, which is
# what lets a rephrased prompt stay while a long prompt about something else cannot. Both floors sit between measured
# populations: the weakest genuine rephrase pairs score ratio 0.46 / coverage 0.43, while the space-pirate intruders
# scored 0.04 (the two-word prompt) and 0.12 (the "show exactly 9 ..." instruction).
MIN_MEMBER_COVERAGE = 0.30
# A second attachment path, beside the anchor comparison that `DEFAULT_THRESHOLD` governs. A candidate that misses every
# anchor still joins a folder if it is this similar to *any* member - which is what an older, shorter anchor fails to
# notice. Measured on the real gallery (1,783 distinct prompts): the poodle family, split only by prompt length, becomes
# one folder of 8 and the folder count moves 97 -> 96, while the e2e paraphrase recall of `test_groups.py` is unchanged
# because the 0.20 anchor path is untouched. At 0.55 the gallery collapses to 73 folders; 0.75 loses a member again.
MEMBER_MATCH = 0.70
DEFAULT_RECENT = 24
MIN_GROUP = 2                  # a "folder" of one picture is just the picture

STOP = set('''a an the and or of to in on at by for with from as is are was were be been being this that these those it
its into over under near above below behind beside between across through during before after while very more most
some any all each both few many much no not only own same so than too can will just should now one two three photo
photograph image picture render shot style high quality detailed detail realistic'''.split())

# Hyphens split, because the same subject is written both ways in the same gallery ("space-pirate cat-woman" against
# "space pirate lady cat", "wide-shot" against "wide shot") and a hyphen kept them apart.
_WORD = re.compile(r"[a-z][a-z']+")

# The rephrase button swaps words for synonyms on purpose, so two phrasings of one scene can share almost no surface
# tokens ("a weathered brick wall" against "a time-worn masonry partition"). Reusing the app's own dictionary as a soft
# match is what keeps those together without lowering the threshold until unrelated scenes merge.
SYNONYM_WEIGHT = 0.45
try:
    import rephrase as _rp
    _SYN = {k: {o.lower() for o in v} for k, v in _rp.LOOKUP.items()}
except Exception:                                   # the grouping must work even without the dictionary
    _SYN = {}


def _related(t, u):
    a, b = _SYN.get(t), _SYN.get(u)
    if not a and not b:
        return False
    return (b is not None and t in b) or (a is not None and u in a)


# Prompts written as briefs carry structural labels, and they are formatting rather than content: DESCRIPTION:,
# COMPOSITION:, CAMERA:, LIGHT:, MOOD:, SOUND: (33 prompts each in this gallery). They repeat across entirely different
# scenes, so a header is both a false similarity signal and a plausible name - one folder of 142 pictures was called
# "• Multiple Angles: Front, side, 3/4 front..." purely from its section line.
#
# An all-caps label followed by ordinary text is the strongest signal, and it is deliberately narrow: a real sentence
# that happens to end in a colon ("The pages are full of the room: a sofa") is left alone, and so is title-case prose
# ("Footwear: black boots") unless the label stands on its own line or follows a bullet, which is how a section header
# is written. Measured against the gallery, the all-caps rule covers every genuine header there; the standalone and
# bulleted rules add "Multiple Angles:" without touching sentences.
_CAPS_LABEL = re.compile(r'(?:^|\n|\u2022|\*)\s*([A-Z][A-Z0-9 \'&/\-]{2,40}?)\s*:\s*')
_BULLET_LABEL = re.compile(r"(?:\u2022|\*)\s*([A-Z][A-Za-z /'&\-]{1,40}?)\s*:\s*")
_LABEL_ONLY_LINE = re.compile(r"(?m)^\s*(?:[\u2022\-\*]\s*)?([A-Z][A-Za-z /'&\-]{1,40}?)\s*:\s*$")
_SLUG = re.compile(r'(?m)^\s*(?:INT|EXT|INT\./EXT\.)\s*\.?\s*$')
# A prompt may carry the page it was copied from ("...(http://www.oafe.net/yo/mcxfs.php)"). That is not the subject, and
# it once named a folder.
_URL = re.compile(r'\(?\bhttps?://[^\s)]+\)?', re.I)
# Caption scaffolding: the Describe step writes every picture's caption the same way, and those words were linking families.
# "a full-body character concept sheet from multiple angles. It features a female warrior ... The character is depicted in
# distinct views: a full front view, a three-quarter view and a back view." says nothing about *which* warrior.
_CAPTION_SCAFFOLD = re.compile(
    r'a full-body character concept sheet from multiple angles\.?'
    r'|showcasing a full-body character concept sheet\.?'
    r'|the character is depicted in distinct views[^.]*\.?'
    r'|it features an?\s+', re.I)
_SENTENCE_OPENERS = ('the', 'a', 'an', 'on', 'in', 'at', 'around', 'beside', 'behind', 'with', 'this', 'these', 'her',
                     'his', 'their', 'it', 'there')


def _is_header(match, caps_only=False):
    """A match is a header when it is all caps (the strong signal), or short and clearly a label rather than prose."""
    label = match.group(1).strip()
    words = label.split()
    if len(words) > 4 or words[0].lower() in _SENTENCE_OPENERS:
        return False
    if label.upper() == label and any(c.isalpha() for c in label):
        return True                      # CAMERA:, MOOD:, PROPORTIONS:
    return not caps_only                 # title case, only where the position already says "section" (line/bullet)


# The character-sheet block: "shehulk • Multiple Angles: Front, side, 3/4 front, and back views. • Proportions: Guides
# showing the character's height using head-count measurements ... • Expressions ... • Poses and Gestures ...". It is a
# template, identical for every character, and it carried enough rare words to merge shehulk, a turtle and a space-pirate
# cat into one folder of 47 prompts. Everything from the first section on is not about the subject.
_SHEET_SECTION = re.compile(
    r'(?:\u2022|\n|^)\s*(?:Multiple Angles|Proportions|Expressions|Poses? and Gestures|Signature Actions?'
    r'|Design Notes|Silhouette|Line ?up|Head ?count|Character Sheet)\b', re.I)


def strip_headers(text):
    """Drop generic section labels from a prompt, leaving the sentences that actually describe the picture."""
    t = text or ''
    # a template block describes the *sheet*, not the character: cut it before its first section
    head = _SHEET_SECTION.split(t, maxsplit=1)[0]
    if head.strip():
        t = head
    t = _CAPTION_SCAFFOLD.sub(' ', t)
    t = _CAPS_LABEL.sub(lambda m: '\n' if _is_header(m, caps_only=True) else m.group(0), t)
    t = _LABEL_ONLY_LINE.sub(lambda m: '' if _is_header(m) else m.group(0), t)
    t = _BULLET_LABEL.sub(lambda m: '\n' if _is_header(m) else m.group(0), t)
    t = _SLUG.sub(' ', t)
    return _URL.sub(' ', t)


def tokens(text):
    """Content tokens, crudely stemmed - rephrase swaps plurals and gerunds, so 'columns' and 'column' must meet."""
    out = []
    for t in _WORD.findall(strip_headers(text).lower()):
        if t in STOP or len(t) < 3:
            continue
        if t.endswith('ies') and len(t) > 4:
            t = t[:-3] + 'y'
        elif t.endswith('es') and len(t) > 4:
            t = t[:-2]
        elif t.endswith('s') and len(t) > 3:
            t = t[:-1]
        elif t.endswith('ing') and len(t) > 5:
            t = t[:-3]
        elif t.endswith('ed') and len(t) > 4:
            t = t[:-2]
        out.append(t)
    return out


def fit_lexicon(prompts):
    """Document frequencies over a *corpus*. idf must come from many prompts, never from the pair being compared:
    over two documents a shared token has df=2 and a unique one df=1, so the sharing is penalised and near-identical
    prompts score below any sensible threshold. That was a real bug, found by the rephrase-stability test."""
    df = Counter()
    for p in prompts:
        df.update(set(tokens(p)))
    n = len(prompts)
    return {'n': n, 'df': df, 'idf': {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}}


# A prompt is written subject first, then style: 142 prompts here end in the identical clause
# ", photograph, natural light, shallow depth of field". Because that tail is shared by *different* subjects, weighting
# every token equally merged life-drawing figure models into the steampunk folder (196 members, only 98 mentioning
# steampunk). Clause position is the cheap, domain-correct discriminator: the head clause is what the picture is of.
CLAUSE_WEIGHTS = (1.0, 0.6, 0.35)


def token_weights(text):
    """token -> the strongest clause weight it appears in (first clause 1.0, later clauses fall away)."""
    out = {}
    clauses = re.split(r'[,;.!?]|\n', strip_headers(text).lower())
    for i, clause in enumerate(clauses):
        w = CLAUSE_WEIGHTS[min(i, len(CLAUSE_WEIGHTS) - 1)]
        for t in tokens(clause):
            if w > out.get(t, 0.0):
                out[t] = w
    return out


def vector(text, lexicon, expand_synonyms=True):
    """idf-weighted unit vector for one prompt; synonyms of its words are added at a lower weight.

    With expansion on, "brickwork" contributes to "masonry" at SYNONYM_WEIGHT, so a rephrased prompt still lands near
    its original without the threshold being lowered for everyone.

    The stated count is dropped first: it is one word among a dozen, so similarity treats "five women ..." and "a dozen
    women ..." as the same subject - and on a short prompt it is otherwise the *loudest* word, which split one scene into
    several folders. The count still reaches the folder name, and a folder that disagrees on it states none.
    """
    text = without_counts(text) or text
    bag = Counter(tokens(text))
    weights = token_weights(text)
    idf = lexicon['idf']
    v = {t: (1 + math.log(c)) * idf[t] * weights.get(t, 1.0) for t, c in bag.items() if t in idf}
    if expand_synonyms and _SYN:
        for t, c in bag.items():
            for o in _SYN.get(t, ()):
                if o in idf and o not in v:
                    v[o] = SYNONYM_WEIGHT * (1 + math.log(c)) * idf[o] * weights.get(t, 1.0)
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


def build_vectors(prompts, lexicon=None):
    """One lexioned vector per prompt. Passing no lexicon fits one to these prompts, which is right only when they
    *are* the corpus; a two-item list is not."""
    lexicon = lexicon or fit_lexicon(prompts)
    return [vector(p, lexicon) for p in prompts]


def raw_vector(text, lexicon, expand_synonyms=True):
    """The same bag as `vector`, before normalisation - the honest measure of how much it says."""
    bag = Counter(tokens(text))
    weights = token_weights(text)
    idf = lexicon['idf']
    v = {t: (1 + math.log(c)) * idf[t] * weights.get(t, 1.0) for t, c in bag.items() if t in idf}
    if expand_synonyms and _SYN:
        for t, c in bag.items():
            for o in _SYN.get(t, ()):
                if o in idf and o not in v:
                    v[o] = SYNONYM_WEIGHT * (1 + math.log(c)) * idf[o] * weights.get(t, 1.0)
    return v


def shared_mass(ra, rb):
    """(shared weight, coverage) where coverage must hold in *both* directions.

    One direction is not enough: "a woman not a lady" is two words, and a specific space-pirate prompt contains one of
    them, so the vague prompt looks fully covered by the specific one. A folder may only absorb a newcomer when it
    accounts for that newcomer's content *and* the newcomer accounts for the folder's - otherwise the vague prompt
    becomes the representative and drags the specific ones in.
    """
    shared = sum(x for t, x in ra.items() if t in rb)
    ta = sum(ra.values()) or 1.0
    tb = sum(rb.values()) or 1.0
    return shared, min(shared / ta, shared / tb)


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def similarity(a, b, lexicon, cache=None):
    """Cosine similarity of two prompt strings under a fitted lexicon."""
    key = (a, b) if a <= b else (b, a)
    if cache is not None and key in cache:
        return cache[key]
    s = cosine(vector(a, lexicon), vector(b, lexicon))
    if cache is not None:
        cache[key] = s
    return s


def group_texts(prompts, threshold=DEFAULT_THRESHOLD, lexicon=None, _depth=0):
    """Greedy incremental grouping in the order given. Returns a list of member-index lists, in that order.

    Order matters: the caller passes oldest first so a group is anchored by its oldest member and the folder order is
    stable as new pictures arrive.
    """
    lexicon = lexicon or fit_lexicon(prompts)
    vecs = build_vectors(prompts, lexicon)
    raws = [raw_vector(p, lexicon) for p in prompts]
    labels = [-1] * len(prompts)
    buckets = []                     # cluster -> its member indices
    for i, vec in enumerate(vecs):
        best, best_k = 0.0, -1
        for k, members in enumerate(buckets):
            s = cosine(vec, vecs[members[0]])
            if s > best:
                best, best_k = s, k
        if best >= threshold and best_k >= 0:
            labels[i] = best_k
            buckets[best_k].append(i)
            continue
        else:
            buckets.append([i])
            labels[i] = len(buckets) - 1

    groups = {}
    for i, lab in enumerate(labels):
        groups.setdefault(lab, []).append(i)

    # Verification pass, order-independent: a cluster is judged by its most specific member (the one that says the
    # most), and a member that member cannot account for is dropped. This is what keeps a two-word prompt - which
    # scores 0.46 against a long character prompt just by sharing "lady" - out of the folder it did not earn. Rephrased
    # prompts survive on the synonym-aware cosine instead, which is why both tests are here.
    out = []
    rejected = []
    for k in sorted(groups):
        members = groups[k]
        if len(members) < 2:
            out.append(members)
            continue
        # The judge is the cluster's **medoid** - the member closest to all the others - not its most verbose member.
        # Choosing by raw mass is how a different film's long prompt came to judge a scene it does not belong to: 11
        # Fight Club prompts joined a bucket at 0.21-0.25 cosine, their longest member became the core, it evicted the
        # six genuine scene briefs by 0.012 of coverage, and the scene split in two 0.188 apart (2026-10-28). The medoid
        # is what the cluster actually agrees with. Measured over the gallery: 89 -> 87 folders, mean intra-folder
        # cosine 0.859 -> 0.849, leave-one-out 98.78 % -> 98.90 %, the LAFAYETTE scene becomes one folder of 14 with the
        # 11 Fight Club pictures separate, the flow-chart pair and the poodle family are unchanged, and the two style
        # variants of the cyberdeck (10 + 37) merge into one folder of 47.
        core = max(members, key=lambda m: (sum(cosine(vecs[m], vecs[o]) for o in members if o != m), -m))
        kept, dropped = [core], []
        for m in members:
            if m == core:
                continue
            short, long_ = sorted((len(raws[m]), len(raws[core])))
            comparable = long_ and (short / long_) >= MIN_LENGTH_RATIO
            shared = sum(x for t, x in raws[m].items() if t in raws[core])
            explained = shared / (sum(raws[m].values()) or 1.0)      # how much of the member the core accounts for
            if (comparable and explained >= MIN_MEMBER_COVERAGE) or cosine(vecs[m], vecs[core]) >= STRONG_MATCH:
                kept.append(m)
            else:
                dropped.append(m)
        out.append(sorted(kept))
        rejected.extend(dropped)
    # A rejected member is not abandoned: it is re-clustered among the other rejects, because a picture that does not
    # belong in that folder usually belongs in another one (500 of them did on the first attempt, and dropping them to
    # "ungrouped" was worse than the misfiling it fixed).
    if rejected and _depth < 3:
        sub = group_texts([prompts[i] for i in rejected], threshold, lexicon, _depth + 1)
        out.extend([[rejected[j] for j in g] for g in sub])
    elif rejected:
        out.extend([[m] for m in rejected])
    return _merge_close(out, vecs, raws, prompts=prompts)


def _merge_close(groups, vecs, raws, floor=MEMBER_MATCH, prompts=None):
    """Join clusters whose most specific members are near-identical, after clustering rather than during it.

    This is the fix for a measured defect: the anchor is the *oldest* member, and an oldest prompt is often the shortest
    one. Three poodle prompts anchored a folder; the five longer ones - "artists watercolor rendering of a black medium
    size standard poodle ..." - opened a second, the first of them scoring 0.01 against that anchor, even though the
    strongest cross-folder pair measured 0.87 while each folder's own mean was 0.83 and 0.80. Comparing every cluster's
    core (its most specific member) closes the gap in one pass, and it cannot chain the way a member-level threshold
    does: on the real gallery, cores at 0.70 merge 7 pairs (93 -> 86 folders, biggest 206 -> 223, mean intra-folder
    cosine 0.873 -> 0.875), while comparing every member at the same 0.70 collapses the gallery to 73 folders with a
    342-picture folder.

    Pairs are taken strongest first and merged with a union-find, so a chain of weak links cannot walk the whole corpus
    together; each core is compared once.
    """
    if len(groups) < 2:
        return groups
    cores = [max(g, key=lambda m: (sum(raws[m].values()), -m)) for g in groups]
    pairs = []
    for a in range(len(groups)):
        for b in range(a + 1, len(groups)):
            s = cosine(vecs[cores[a]], vecs[cores[b]])
            same_name = prompts is not None and _names_the_same_thing(prompts[cores[a]], prompts[cores[b]])
            if s >= floor or same_name:
                pairs.append((s, a, b))
    if not pairs:
        return groups
    pairs.sort(reverse=True)
    parent = list(range(len(groups)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _s, a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    merged = {}
    for i, g in enumerate(groups):
        merged.setdefault(find(i), []).extend(g)
    return [sorted(v) for v in merged.values()]


def _names_the_same_thing(a, b, min_words=2):
    """True when one prompt *is* the opening of the other, word for word.

    A counter-case to the cosine floor, measured on the real gallery: "flow chart" (2 pictures) sat in its own folder
    beside "flow chart with start, processing and finish nodes" (24) - core-to-core 0.67, just under the 0.70 merge
    floor, although one name is literally the other with less detail. Six folder pairs in the gallery have that shape.
    A word-prefix test is conservative where lowering the floor is not: it cannot merge "steampunk cyberdeck" with
    "cyberdeck portable computer ... waterproof case" (0.69 by cosine, different phrasing), which a 0.65 floor would.
    """
    wa = [w for w in re.findall(r"[a-z][a-z'-]+", (a or '').lower()) if w not in STOP]
    wb = [w for w in re.findall(r"[a-z][a-z'-]+", (b or '').lower()) if w not in STOP]
    if len(wa) < min_words or len(wb) < min_words:
        return False
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len(short) < len(long_) and long_[:len(short)] == short


def shared_prefix(texts):
    """The opening that every one of `texts` shares, at least 40 characters long, or ''.

    Compared on whitespace-normalised, case-folded text and cut back to a word boundary, so the answer can be sliced off
    each original prompt and what remains is what actually differs.
    """
    norm = [' '.join((t or '').split()) for t in texts if (t or '').strip()]
    if len(norm) < 2:
        return ''
    first = norm[0].lower()
    n = len(first)
    for other in norm[1:]:
        low = other.lower()
        i, limit = 0, min(n, len(low))
        while i < limit and low[i] == first[i]:
            i += 1
        n = i
        if not n:
            return ''
    prefix = first[:n]
    cut = prefix.rstrip().rfind(' ')
    return prefix[:cut + 1] if cut > 0 else ''


def label_for(prompts, members, width=60, lexicon=None, roots=None):
    """A folder name taken from the member that best represents the folder - never invented, never a bare fragment.

    It used to be the *oldest* member's prompt, which is how a folder of 303 pictures ended up called "less intense
    eyes": that phrase was simply the first thing saved. Now the name comes from a member that carries the folder's
    most distinctive shared token, that is not a caption template, and that is central to the folder - so a three-of-nine
    minority cannot name it. A prompt with fewer than three content words is never chosen while a fuller one exists.
    """
    texts = [(prompts[m] or '').strip() for m in members]
    specificity = {}
    # a family carries its roots: the instruction that last touched a picture is not what the folder is about.
    # Named by the most *specific* root, not the most popular one: "Chiaroscuro, photograph, natural light, shallow depth of
    # field" appears far more often than the subject it accompanies, and naming a 61-picture family after the camera
    # fragment is how a good merge looked wrong (measured: 'light' is in 39 % of this gallery's prompts).
    if roots:
        root_texts = [' '.join(r.split()) for r in roots if r and str(r).strip()]
        if root_texts:
            try:
                idf_roots = (lexicon or fit_lexicon(root_texts))['idf']
            except Exception:
                idf_roots = {}
            texts = root_texts
            specificity = {text: sum(idf_roots.get(w, 0.0) for w in set(tokens(text))) for text in root_texts}
    if not texts:
        return '(no prompt recorded)'
    try:
        idf = (lexicon or fit_lexicon(texts))['idf']
    except Exception:
        idf = {}
    toks = [set(tokens(t)) for t in texts]          # tokenise once: this was 216k token calls per plan
    nlens = [len(tk) for tk in toks]

    counts = Counter()
    for tk in toks:
        counts.update(tk)
    need = max(2, int(0.6 * len(texts)))
    core = sorted((t for t, c in counts.items() if c >= need), key=lambda t: -idf.get(t, 0.0))[:3]
    core_set = set(core)

    # Captions written by a model open with meta-talk, not a subject: "This image is a detailed digital illustration
    # showcasing...". A folder named that way tells the eye nothing, so such a head is only used when every member has
    # one - i.e. the folder really is a set of captions.
    template = re.compile(r'^(this|the)\s+(image|picture|photo|shots?)\b|^(pictured|shown here|depicted|in this)')

    frequency = Counter(' '.join(t.split()) for t in texts)

    def rank(i):
        shared = toks[i] & core_set
        head = ' '.join(texts[i].split()).lower()
        return (1 if shared else 0,
                0 if template.match(head) else 1,
                round(specificity.get(texts[i], 0.0), 2),
                frequency.get(' '.join(texts[i].split()), 0),
                sum(idf.get(t, 0.0) for t in shared),
                nlens[i])

    cands = [i for i in range(len(texts)) if nlens[i] >= 3] or list(range(len(texts)))
    top = sorted(cands, key=rank, reverse=True)[:5]   # centrality is a tie-break among the plausible, not a full scan
    if len(top) > 1:
        lex = lexicon or fit_lexicon(texts)
        step = max(1, len(texts) // 40)
        sample = list(range(0, len(texts), step))
        vecs = {i: vector(texts[i], lex) for i in set(top) | set(sample)}
        def centrality(i):
            return sum(cosine(vecs[i], vecs[j]) for j in sample if j != i)
        best = max(top, key=lambda i: (rank(i), round(centrality(i), 3)))
    else:
        best = top[0] if top else 0

    text = ' '.join(texts[best].split())
    # a sheet template describes the sheet; the name must be the character it is a sheet of ("shehulk" was named after a
    # bullet, "• Proportions: Guides showing the character's height ...")
    text = _URL.sub(' ', text)
    sheet_head = _SHEET_SECTION.split(text, maxsplit=1)[0].strip(' \u2022-')
    if len(tokens(sheet_head)) >= 1:
        text = sheet_head
    for _ in range(6):                     # DESCRIPTION:\nA portrait...  ->  A portrait...
        m = re.match(r"^(?:[\u2022\-\*]\s*)?([A-Z][A-Za-z /'-]{1,40}?)\s*:\s*", text)
        if not m or len(m.group(1).split()) > 4:
            break
        text = text[m.end():]
    # An edit instruction names the edit, not the picture: "keep all poses as they are but replace with <the subject>"
    # truncated at 60 characters says nothing about the folder (measured on 178 pictures of one 3d-shapes run). The
    # payload is the subject, so the name starts there.
    payload = re.search(r'\breplace(?:d)?\s+with\s+(.{12,})|\bchange[sd]?\s+(?:them|it|these)\s+into\s+(.{12,})', text)
    if payload:
        text = (payload.group(1) or payload.group(2) or '').strip()
    # a screenplay slug is not a name either: "INT. A dark office..." must be named by the office, not by "INT."
    text = re.sub(r'^(?:INT|EXT|INT\./EXT\.)\.?\s*', '', text)
    # and a first sentence that is only a fragment ("INT.") is skipped for the first one that says something
    head = ''
    for part in re.split(r'(?<=[.!?])\s', text):
        if len(tokens(part)) >= 2:
            head = part
            break
    if not head:
        head = re.split(r'(?<=[.!?])\s', text)[0] if text else ''
    if not head:
        head = text
    # A run of edit instructions opens with the same words and differs only after them - "keep all three poses standing
    # as they are but replace with <what changes>". Truncated at the shared opening, four different prompts got one name
    # (measured: 178 pictures, 4 prompts, the label was the instruction and the difference was always cut off). When the
    # members share a long opening, the folder is named by the part that differs.
    opening = shared_prefix(texts)
    if len(opening) >= 40:
        flat = _URL.sub(' ', ' '.join(texts[best].split()))
        tail = flat[len(opening):].strip(' .,;:-') if len(flat) > len(opening) else ''
        if len(tail) < 12:
            longest = _URL.sub(' ', max(texts, key=len))
            tail = ' '.join(longest.split())[len(opening):].strip(' .,;:-')
        if len(tail) >= 12:
            head = '\u2026' + tail
    if width and len(head) > width:
        cut = head[:width].rsplit(' ', 1)[0]
        head = (cut or head[:width]) + '\u2026'
    return head or '(no prompt recorded)'



# -- stated head counts -----------------------------------------------------------------------------------------------
# A prompt that says "three ... women" and one that says "exactly six adult women" can be different pictures, and the text
# similarity cannot tell, because the count is one word among many; so it is read explicitly.
#
# It is *not* used to split a folder any more (2026-10-30). Splitting made five folders of one scene - "five naked women
# floating in a circle", "a dozen naked women ...", "a dozen diverse naked women ...", "3 naked women ..." and the
# count-free form - which a reader calls one series of attempts. The count now only reaches the name, and a folder whose
# members disagree states none (see without_counts and plan).
COUNT_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
               'ten': 10, 'eleven': 11, 'twelve': 12, 'dozen': 12}
PEOPLE_WORDS = ('adult', 'woman', 'women', 'man', 'men', 'people', 'person', 'persons', 'figure', 'figures', 'character',
                'characters', 'player', 'players', 'girl', 'girls', 'boy', 'boys', 'face', 'faces')
_PREPOSITIONS = r'(?:above|behind|near|beside|by|on|in|at|with|from|under|over|across)\b'


# A prompt states its cast near the front. Measured: two 1,600-character prompts that never state a group size
# ("Pictured are women. Each clothed in ...") were being read as three, because a numeral deep in the tail sat within a
# few words of a people noun. Beyond this many characters the phrase is about something else, so the prompt is treated
# as stating nothing - which is not measured, rather than measured wrongly.
COUNT_HEAD = 240


def stated_count(prompt):
    """The single head count a prompt states, or None when it states several (ambiguous) or none.

    The *earliest* phrase wins - iterating the words instead made "six women ... three layers" read as three - and a
    people noun may sit a few adjectives away ("three very strong fit toned muscled adult women") but not across a
    preposition: "three green lamps above two women" is about the lamps.
    """
    low = ' '.join((prompt or '').split()).lower()
    found = []
    for word, number in COUNT_WORDS.items():
        pattern = r'\b%s\b\s+(?:(?!%s)\w+\s+){0,8}(?:%s)\b' % (word, _PREPOSITIONS, '|'.join(PEOPLE_WORDS))
        for match in re.finditer(pattern, low):
            found.append((match.start(), number))
    # only what the head of the prompt says counts: a numeral in the tail is a different subject
    # a numeral states one just as plainly ("3 naked women"), and it is what the gallery's own prompts use
    for match in re.finditer(r'\b(\d{1,2})\b\s+(?:(?!%s)\w+\s+){0,8}(?:%s)\b' % (_PREPOSITIONS, '|'.join(PEOPLE_WORDS)), low):
        found.append((match.start(), int(match.group(1))))
    found = [pair for pair in found if pair[0] < COUNT_HEAD]
    if not found:
        return None
    found.sort()
    if len({number for _pos, number in found}) > 1:
        return None                      # several different counts stated: the prompt is ambiguous, so it decides nothing
    return found[0][1]


COUNT_TOKEN = re.compile(r'\b(?:' + '|'.join(COUNT_WORDS) + r'|a dozen|dozen|exactly|\d{1,3})\b', re.I)


def without_counts(prompt):
    """The prompt with its stated count removed - the same subject whether it says "five", "a dozen" or nothing.

    Two pictures made from the same subject with a different count in the sentence are the same folder to a reader
    ("five naked women floating in a circle" and "a dozen naked women floating in a circle" are one series of attempts),
    and the folder's name must not assert a count its members do not all share.
    """
    text = ' '.join((prompt or '').split())
    text = COUNT_TOKEN.sub(' ', text)
    return ' '.join(text.split())


def plan(items, threshold=DEFAULT_THRESHOLD, recent=DEFAULT_RECENT, min_group=MIN_GROUP, keep_lexicon=False):
    """items: list of dicts with at least 'path', 'prompt', and a sortable 'created' (or 'mtime') key, NEWEST first.

    Returns {'recent': [...], 'groups': [{'label', 'items', 'oldest', 'newest'}]}: the newest `recent` pictures
    untouched and in date order, then the folders of the older ones, ordered by their newest picture (the folder most
    recently added to first). Pictures whose prompt is empty are never put in
    a folder - they stay in a trailing 'ungrouped' bucket, because nothing can be said about them.
    """
    order = sorted(items, key=lambda r: r.get('created') or '', reverse=True)
    head, tail = order[:recent], order[recent:]

    with_prompt = [r for r in tail if (r.get('prompt') or '').strip()]
    without = [r for r in tail if not (r.get('prompt') or '').strip()]
    # oldest first, so the anchor of a group is its oldest picture
    with_prompt.sort(key=lambda r: r.get('created') or '')

    groups = []
    # The corpus statistics are fitted on every prompt: the lexicon is what prediction and live_append use to recognise
    # "this new picture belongs to that folder", and fitting it on a reduced set quietly broke both.
    every_prompt = [r['prompt'] for r in with_prompt]
    lexicon = fit_lexicon(every_prompt + [(r.get('prompt') or '') for r in head]) if every_prompt else None

    # A picture is filed under the prompt its SERIES started from, not under the instruction that last touched it. An edit
    # ("add guns, ammo, knives, weapons and armor") describes a change, not a subject, so on its own it makes a folder of
    # five pictures that plainly belong with the pictures they were edited from. The root is recorded (sidecar
    # `lineage_root`), so the roots are clustered once - the same rule, one level up - and every picture follows its root's
    # family. Pictures without a root are grouped by their own text, as before.
    roots = {}
    for r in with_prompt:
        root = ' '.join((r.get('root') or '').split())
        if root:
            roots.setdefault(root, []).append(r)
    if roots:
        families = {}
        root_list = list(roots)
        if len(root_list) > 1:
            # Roots are joined when they describe the same scene, judged on their own idf cosine at a generous floor. The
            # rules tried before this one each failed a real case: containment merged roots that merely shared a heading
            # ("a full-body character concept sheet from multiple angles") and refused roots that paraphrase each other
            # ("exactly six adult women sit in a single shallow semicircle ..." against "... sit cross-legged ..."), while
            # linking on a shared opening chained a quarter of the gallery into one family. Measured at 0.50 on the real
            # gallery: 68 families, largest 173 (unchanged), and every case previously reported is now one folder.
            vectors = {text: vector(text, lexicon) for text in root_list}
            parent = {text: text for text in root_list}

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            for i, text_a in enumerate(root_list):
                for text_b in root_list[i + 1:]:
                    if cosine(vectors[text_a], vectors[text_b]) >= ROOT_LINK:
                        parent[find(text_b)] = find(text_a)
            for text in root_list:
                families[text] = find(text)
        for i, text in enumerate(root_list):
            families.setdefault(i, text)
        by_family = {}
        for text in root_list:
            by_family.setdefault(families[text], []).extend(roots[text])
        family_paths = set()
        for label, rows_here in by_family.items():
            rows_here.sort(key=lambda r: r.get('created') or '')
            family_paths.update(r['path'] for r in rows_here)
            if len(rows_here) < min_group:
                continue
            counts = {stated_count(r.get('prompt')) for r in rows_here} - {None}
            label_prompts = ([without_counts(r['prompt']) for r in rows_here] if len(counts) > 1
                             else [r['prompt'] for r in rows_here])
            groups.append({'label': label_for(label_prompts, range(len(label_prompts)), lexicon=lexicon,
                                              roots=[r.get('root') for r in rows_here]),
                           'title': label_for(label_prompts, range(len(label_prompts)), width=0, lexicon=lexicon,
                                              roots=[r.get('root') for r in rows_here]),
                           'items': rows_here, 'oldest': rows_here[0].get('created') or '',
                           'newest': (rows_here[-1].get('created') or ''),
                           'series': len({tuple(r.get('lineage') or [r['prompt']]) for r in rows_here})})
        with_prompt = [r for r in with_prompt if r['path'] not in family_paths]
    # An "exact prompt" pre-pass used to bucket identical prompts before this pass ran. It was wrong in the direction that
    # matters: an exact bucket *absorbs* its rows, so a near-duplicate one word away ("... diverse naked women ...") went
    # into a different bucket and could never be clustered with it - five folders of one subject. The similarity pass sees
    # everything, and the stated count is handled in the label instead of by splitting.
    if with_prompt and lexicon is not None:
        prompts = [r['prompt'] for r in with_prompt]
        for members in group_texts(prompts, threshold=threshold, lexicon=lexicon):
            if len(members) < min_group:
                continue
            rows = sorted((with_prompt[m] for m in members), key=lambda r: r.get('created') or '')
            # A cluster can hold two different stated counts ("three ... women" and "exactly six adult women"): the count
            # is the subject, so the cluster is split before a label is chosen. Measured on the real gallery: two of 85
            # folders mixed counts and between them held 363 pictures.
            counts = {stated_count(r.get('prompt')) for r in rows} - {None}
            # Counts no longer split a cluster: they made five folders of one subject (measured on the real gallery, 13
            # pictures of one scene landed in 3 folders at every threshold from 0.15 to 0.40). The label carries the truth
            # instead - if the members disagree on the count, the name states none.
            for part in [rows]:
                if len(part) < min_group:
                    continue
                part_prompts = ([without_counts(r['prompt']) for r in part] if len(counts) > 1
                                else [r['prompt'] for r in part])
                # `label` is the short form for the selector; `title` is the same sentence uncut, for the tile
                groups.append({'label': label_for(part_prompts, range(len(part)), lexicon=lexicon),
                               'title': label_for(part_prompts, range(len(part)), width=0, lexicon=lexicon),
                               'items': part, 'oldest': part[0].get('created') or '',
                               'newest': (part[-1].get('created') or '')})
    grouped_paths = {r['path'] for g in groups for r in g['items']}
    leftovers = [r for r in with_prompt if r['path'] not in grouped_paths]
    # Folders are ordered by when they were last touched - the newest picture they hold - not by when they started.
    # A folder order built on the oldest member never moves, which is stable but puts a folder that just received a
    # render in the position it had when it was created. Ties fall back to the label so the order stays deterministic.
    groups.sort(key=lambda g: g['label'])
    groups.sort(key=lambda g: g.get('newest') or '', reverse=True)
    out = {'recent': head, 'groups': groups, 'ungrouped': leftovers + without,
           'threshold': threshold, 'recent_n': recent}
    if keep_lexicon and lexicon is not None:
        out['_lexicon'] = lexicon      # the open-folder view re-matches new pictures against the same corpus statistics
    return out


def predict(text, plan, threshold=DEFAULT_THRESHOLD, margin=0.0):
    """Which folder would a new prompt join? Returns {'index','label','score','margin'} or None.

    Asked *before* a render, so the strip can open the folder the picture is about to land in. Leave-one-out on the real
    gallery - predict each existing prompt against every folder, its own included, without that prompt - puts the right
    folder first for **99.0 %** of 1,756 prompts, with a median top1-top2 margin of 0.42; at a score of 0.85 it is
    99.5 % correct on 97 % of prompts. That is measured on prompts that *are* members, so it says the membership rule is
    decisive for a variant, not that a novel subject lands anywhere: under the threshold this returns None and the
    caller says "new folder".
    """
    lexicon = (plan or {}).get('_lexicon')
    groups = (plan or {}).get('groups') or []
    text = (text or '').strip()
    if not lexicon or not text or not groups:
        return None
    vec = vector(text, lexicon)
    scores = []
    for i, group in enumerate(groups):
        member_vecs = build_vectors([r.get('prompt') or '' for r in group['items']], lexicon)
        scores.append((max((cosine(vec, mv) for mv in member_vecs), default=0.0), i))
    scores.sort(reverse=True)
    best, index = scores[0]
    runner = scores[1][0] if len(scores) > 1 else 0.0
    if best < threshold or (best - runner) < margin:
        return None
    return {'index': index, 'label': groups[index].get('label') or '', 'score': best,
            'margin': best - runner}


def _group_vectors(plan, lexicon):
    """Per-folder member vectors, computed once per plan and cached on it - the live view redraws on every selection."""
    cached = plan.get('_group_vecs')
    if cached is not None and len(cached) == len(plan.get('groups') or []):
        return cached
    out = [build_vectors([r.get('prompt') or '' for r in g['items']], lexicon) for g in plan.get('groups') or []]
    plan['_group_vecs'] = out
    return out


def live_append(group, plan, threshold=None):
    """Pictures that would land in `group` but are not members of it yet - newest first.

    The newest `recent` pictures are deliberately outside every folder, so a render made *while looking at a folder* is
    not in it yet. This answers "which of the pictures outside every folder would land **here**" without touching the
    plan's membership or the disk.

    **A picture is shown by exactly one folder: the one it would actually join.** The first version asked every folder
    separately "would you accept this?" at the clustering's admission threshold - which is deliberately loose, because
    clustering follows it with a verification pass that the live view never had. Measured on this gallery: each of the
    newest 18 pictures appeared in a mean of 4.4 folders, one in 12 of 85, and a render reported as miscategorised sat
    under "two adult women" at cosine **0.208** - 0.008 over the bar - while its real home scored **1.000**. The question
    a live view answers is singular, so the rule is now the argmax over all folders.
    """
    lexicon = plan.get('_lexicon')
    if not lexicon or not group.get('items'):
        return []
    groups = plan.get('groups') or []
    try:
        mine = groups.index(group)
    except ValueError:
        mine = next((i for i, g in enumerate(groups) if g.get('label') == group.get('label')), None)
    if mine is None:
        return []
    vecs = _group_vectors(plan, lexicon)
    taken = {r['path'] for r in group['items']}
    candidates = [r for r in list(plan.get('recent') or []) + list(plan.get('ungrouped') or [])
                  if r.get('path') not in taken and (r.get('prompt') or '').strip()]
    if not candidates or not vecs:
        return []
    limit = (plan.get('threshold') or DEFAULT_THRESHOLD) if threshold is None else threshold
    hits = []
    for row in candidates:
        vec = vector(row['prompt'], lexicon)
        scores = [max((cosine(vec, mv) for mv in vs), default=0.0) for vs in vecs]
        best = max(scores)
        if best >= limit and scores.index(best) == mine:
            hits.append(row)
    hits.sort(key=lambda r: r.get('created') or '', reverse=True)
    return hits

def self_test():
    """The three scenes from the brief plus controls, checked against known truth. No model, no disk."""
    scenes = {
        'mountain': 'three women standing on a mountain top at sunrise, wide landscape',
        'milkyway': 'a multi ethnic group of friends lying under the milky way, night sky',
        'sofa': 'two women kissing on a leather sofa in a warm living room',
        'pool': 'an olympic swimming pool seen from above, lane ropes, blue tiles',
        'forest': 'a misty pine forest with a narrow path, cold light, ferns',
        'kitchen': 'a busy restaurant kitchen at service, steel surfaces, steam',
    }
    texts, truth = [], []
    for name, base in scenes.items():
        variants = [base,
                    base.replace('three women', '3 women'),
                    base.replace('multi ethnic', 'multiethnic'),
                    base.upper(),
                    '  ' + base + '  ',
                    base.replace('standing on', 'up on'),
                    base + ', highly detailed']
        for v in variants:
            texts.append(v)
            truth.append(name)
    groups = group_texts(texts)
    pairs_ok = True
    for gi, g in enumerate(groups):
        fams = {truth[i] for i in g}
        if len(fams) != 1:
            pairs_ok = False
    covered = {truth[i] for g in groups if len(g) > 1 for i in g}
    return {'groups': len(groups), 'pure': pairs_ok, 'families_covered': len(covered),
            'of_families': len(scenes), 'sizes': sorted((len(g) for g in groups), reverse=True)}


if __name__ == '__main__':
    print(self_test())
