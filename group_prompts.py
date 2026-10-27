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

DEFAULT_THRESHOLD = 0.20
DEFAULT_RECENT = 18
MIN_GROUP = 2                  # a "folder" of one picture is just the picture

STOP = set('''a an the and or of to in on at by for with from as is are was were be been being this that these those it
its into over under near above below behind beside between across through during before after while very more most
some any all each both few many much no not only own same so than too can will just should now one two three photo
photograph image picture render shot style high quality detailed detail realistic'''.split())

_WORD = re.compile(r"[a-z][a-z'-]+")

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


def tokens(text):
    """Content tokens, crudely stemmed - rephrase swaps plurals and gerunds, so 'columns' and 'column' must meet."""
    out = []
    for t in _WORD.findall((text or '').lower()):
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
    clauses = re.split(r'[,;.!?]|\n', (text or '').lower())
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
    """
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


def group_texts(prompts, threshold=DEFAULT_THRESHOLD, lexicon=None):
    """Greedy incremental grouping in the order given. Returns a list of member-index lists, in that order.

    Order matters: the caller passes oldest first so a group is anchored by its oldest member and the folder order is
    stable as new pictures arrive.
    """
    lexicon = lexicon or fit_lexicon(prompts)
    vecs = build_vectors(prompts, lexicon)
    labels = [-1] * len(prompts)
    reps = []
    for i, vec in enumerate(vecs):
        best, best_k = 0.0, -1
        for k, r in enumerate(reps):
            s = cosine(vec, vecs[r])
            if s > best:
                best, best_k = s, k
        if best >= threshold and best_k >= 0:
            labels[i] = best_k
        else:
            reps.append(i)
            labels[i] = len(reps) - 1
    groups = {}
    for i, lab in enumerate(labels):
        groups.setdefault(lab, []).append(i)
    return [groups[k] for k in sorted(groups)]


def label_for(prompts, members, width=60):
    """A folder name taken from the member that best represents the folder - never invented, never a bare fragment.

    It used to be the *oldest* member's prompt, which is how a folder of 303 pictures ended up called "less intense
    eyes": that phrase was simply the first thing saved. Now the label comes from the medoid unless a member carries the
    folder's most distinctive shared token, and a prompt with fewer than three content words is never chosen while a
    fuller one exists - "less intense eyes" identifies nothing.
    """
    texts = [(prompts[m] or '').strip() for m in members]
    if not texts:
        return '(no prompt recorded)'
    idf = {}
    try:
        idf = fit_lexicon(texts)['idf']
    except Exception:
        pass
    # the folder's own core: tokens in most of its members, rarest first
    counts = Counter()
    for t in texts:
        counts.update(set(tokens(t)))
    need = max(2, int(0.6 * len(texts)))
    core = [t for t, c in counts.items() if c >= need]
    core.sort(key=lambda t: -(idf.get(t, 0.0)))
    core = core[:3]

    def content_len(t):
        return len(tokens(t))

    # Captions written by a model open with meta-talk, not a subject: "This image is a detailed digital illustration
    # showcasing...". A folder named that way tells the eye nothing, so such a head is only used when every member
    # has one - i.e. the folder really is a set of captions.
    template = re.compile(r'^(this|the)\s+(image|picture|photo|shots?)\b|^(pictured|shown here|depicted|in this)')

    cands = [i for i, t in enumerate(texts) if content_len(t) >= 3] or list(range(len(texts)))
    def score(i):
        toks = set(tokens(texts[i]))
        shared = toks & set(core)
        return (1 if shared else 0,
                0 if template.match(' '.join(texts[i].split()).lower()) else 1,
                sum(idf.get(t, 0.0) for t in shared),
                content_len(texts[i]))
    best = max(cands, key=score) if cands else 0

    text = ' '.join(texts[best].split())
    head = re.split(r'(?<=[.!?])\s', text)[0] if text else ''
    if not head:
        head = text
    if len(head) > width:
        cut = head[:width].rsplit(' ', 1)[0]
        head = (cut or head[:width]) + '\u2026'
    return head or '(no prompt recorded)'


def plan(items, threshold=DEFAULT_THRESHOLD, recent=DEFAULT_RECENT, min_group=MIN_GROUP):
    """items: list of dicts with at least 'path', 'prompt', and a sortable 'created' (or 'mtime') key, NEWEST first.

    Returns {'recent': [...], 'groups': [{'label', 'items', 'oldest'}]}: the newest `recent` pictures untouched and in
    date order, then the folders of the older ones, oldest group first. Pictures whose prompt is empty are never put in
    a folder - they stay in a trailing 'ungrouped' bucket, because nothing can be said about them.
    """
    order = sorted(items, key=lambda r: r.get('created') or '', reverse=True)
    head, tail = order[:recent], order[recent:]

    with_prompt = [r for r in tail if (r.get('prompt') or '').strip()]
    without = [r for r in tail if not (r.get('prompt') or '').strip()]
    # oldest first, so the anchor of a group is its oldest picture
    with_prompt.sort(key=lambda r: r.get('created') or '')

    groups = []
    if with_prompt:
        prompts = [r['prompt'] for r in with_prompt]
        lexicon = fit_lexicon(prompts + [(r.get('prompt') or '') for r in head])
        for members in group_texts(prompts, threshold=threshold, lexicon=lexicon):
            if len(members) < min_group:
                continue
            rows = sorted((with_prompt[m] for m in members), key=lambda r: r.get('created') or '')
            groups.append({'label': label_for(prompts, members), 'items': rows,
                           'oldest': rows[0].get('created') or ''})
    grouped_paths = {r['path'] for g in groups for r in g['items']}
    leftovers = [r for r in with_prompt if r['path'] not in grouped_paths]
    groups.sort(key=lambda g: g['oldest'])
    return {'recent': head, 'groups': groups, 'ungrouped': leftovers + without,
            'threshold': threshold, 'recent_n': recent}


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
