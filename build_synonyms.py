"""build_synonyms.py - build data/synonyms.json from WordNet for the prompt rephraser.

WordNet's synsets give sense-level synonyms, which is what a prompt needs: words that mean the same thing in the same
sense, not loose associations. The full database is ~148k headwords; a prompt does not need that, so the vocabulary is
scoped to

  * words already used in this machine's prompts (read from the sidecar JSONs), and
  * a built-in list of vocabulary that image prompts actually use (materials, light, weather, subjects, qualities).

Scope keeps the file small enough to load instantly and makes the alternatives relevant - "rendered" should suggest
"depicted", not "melted".

    python build_synonyms.py                      # uses %TEMP%/WNdb-3.0.tar.gz if present, else downloads
    python build_synonyms.py --corpus C:/Users/kevin/Pictures/zimage-ui --out data/synonyms.json

WordNet 3.0 data is distributed by Princeton under the WordNet licence; see data/WORDNET-LICENSE.txt.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import urllib.request
from datetime import datetime
from pathlib import Path

WORDNET_URL = 'https://wordnetcode.princeton.edu/3.0/WNdb-3.0.tar.gz'

# Visual vocabulary: what image prompts are made of, and the *only* words allowed as a replacement. WordNet is tuned
# to prose, so unrestricted it offers "red -> loss" (in the red) and "dog -> track, tag" (to dog someone). Allowing only
# visual vocabulary removes those without hand-checking thousands of entries.
COMMON = """
photo photograph picture image render rendering shot frame view scene closeup close-up wide portrait landscape still
macro panorama composition crop angle perspective foreground background subject still-life
black white red orange yellow green blue purple pink brown grey gray beige teal turquoise gold silver bronze copper
crimson scarlet amber emerald azure violet indigo maroon magenta cyan lilac lavender mint coral peach cream ivory
charcoal slate ochre sienna umber beige pastel muted saturated bright vivid pale light dark deep rich faded bleached
sunrise sunset dawn dusk noon midnight night day morning evening golden twilight glow lit illuminated shadow shade
sky cloud clouds rain snow fog mist storm wind sun moon stars clear overcast hazy gloomy bright sunny dappled
sunbeam beam ray backlight backlit rim silhouette flare sparkle glitter shine glimmer shimmer glow lantern neon
candlelight moonlight starlight daylight lamplight firelight
forest woods woodland tree trees leaf leaves branch branches trunk bark root grass field meadow hill hillside
mountain mountains valley river stream lake sea ocean beach shore coast cliff rock rocks stone stones sand desert
dune dunes island pond waterfall path trail road street lane alley bridge pavement cobble cobblestone gravel soil
mud moss ivy vine hedge bush shrub fern mossy canopy grove thicket orchard vineyard garden park meadow marsh swamp
lagoon reef glacier tundra plateau canyon ridge basin cave hollow creek spring pool bay harbour harbor pier dock
building buildings house home cottage cabin tower castle wall walls brick brickwork concrete stonework facade wood
wooden metal steel iron glass plastic paper fabric cloth leather marble granite slate tile tiles rusty rusted
weathered worn old aged antique new clean tidy dusty grimy shabby broken cracked crumbling ruined derelict decayed
pristine immaculate polished rough coarse smooth soft hard sharp blunt shiny glossy matte reflective wet dry damp
soaked soaked soaked soaked translucent transparent opaque
room rooms kitchen bedroom bathroom office studio workshop library shop cafe restaurant hall corridor stairs stairway
doorway door window windows table desk chair chairs sofa couch bed shelf shelves cupboard cabinet wardrobe dresser
fireplace hearth lamp lamps candle candles mirror rug carpet curtain curtains ceiling floor floors wall walls
countertop sink stove oven fridge bathtub shower toilet towel towels cushion cushions pillow pillows blanket duvet
vase plant pots clock frame poster bookshelf bookcase bench stool pouf basket crate box barrel ladder rope chain
bucket broom mop sink tap faucet jug mug cup cups teapot kettle bottle bottles jar plate plates bowl bowls
glass glasses wine coffee tea bread cake pie fruit apple apples orange orange lemon banana grapes berries
person people man woman child kid girl boy teenager adult elderly crowd crowd figure figures silhouette
face faces hand hands arm arms leg legs hair eyes smile smiling laughing frowning staring looking gazing facing
leaning standing sitting lying fallen flying running walking riding holding gripping carrying bending reaching
climbing crouching kneeling resting propped tilted upright seated perched
cat cats dog dogs horse cow sheep goat pig pigs fish bird birds owl eagle crow sparrow wolf fox bear deer rabbit
mouse mice insect butterfly bee dragonfly spider snake lizard frog turtle dolphin whale shark
bicycle bike cycle motorcycle car cars truck van bus train tram boat boats ship ships plane bicycle
modern vintage retro futuristic industrial rustic elegant cosy cozy comfortable spartan ornate plain bare minimal
minimalist simple detailed intricate complex busy chaotic tidy neat cluttered
large small tiny huge giant vast wide narrow tall short long thick thin deep shallow flat round square angular curved
straight crooked bent twisted
warm cool hot cold freezing freezing icy chilly humid breezy breezy still calm quiet noisy loud busy crowded
peaceful lonely eerie cheerful sad gloomy dramatic moody serene tranquil wild overgrown
texture textured smooth silky coarse fuzzy fleecy bristly ribbed woven knitted quilted patchwork stitched sewn
chipped peeled stained smudged scratched dented rusted corroded polished brushed grainy
morning afternoon evening night seasonal summer winter autumn fall spring sunny rainy snowy windy foggy
close distant near far off-centre centered symmetrical asymmetrical balanced tilted level horizon
""".split()


VISUAL = set(COMMON)


def lemmas_of(line):
    """Parse one WordNet data line and return its lemma list (underscores become spaces)."""
    fields = line.split('|')[0].split()
    if len(fields) < 5:
        return []
    w_cnt = int(fields[3], 16)
    words = []
    idx = 4
    for _ in range(w_cnt):
        if idx >= len(fields):
            break
        words.append(fields[idx].replace('_', ' '))
        idx += 2
    return words


def load_wordnet(tar_path, senses=2):
    """{word: {lemma, ...}} built from the *primary* senses only.

    WordNet's index files list each lemma's synsets in order of frequency, so the first entry is the usual meaning.
    Reading only the first `senses` of each keeps "mug" out of the dictionary (its cup sense has no synonym) instead of
    offering the gullible-person sense, which is what merging every synset produces.
    """
    pos_files = {'n': 'dict/data.noun', 'v': 'dict/data.verb', 'a': 'dict/data.adj', 's': 'dict/data.adj',
                 'r': 'dict/data.adv'}
    index_files = {'n': 'dict/index.noun', 'v': 'dict/index.verb', 'a': 'dict/index.adj', 'r': 'dict/index.adv'}

    want = {}          # (pos, offset) -> set of lemmas, for offsets we actually need
    lemma_senses = {}  # word -> [(pos, offset), ...] in sense order
    with tarfile.open(tar_path) as tf:
        for pos, name in index_files.items():
            try:
                with tf.extractfile(tf.getmember(name)) as fh:
                    for raw in fh:
                        line = raw.decode('utf-8', 'replace')
                        if not line or line.startswith(' '):
                            continue
                        fields = line.split()
                        if len(fields) < 7:
                            continue
                        lemma = fields[0].replace('_', ' ').lower()
                        p_cnt = int(fields[3])
                        sense_cnt = int(fields[4 + p_cnt])
                        offsets = fields[6 + p_cnt:6 + p_cnt + sense_cnt]
                        lemma_senses.setdefault(lemma, [])
                        for off in offsets[:senses]:
                            lemma_senses[lemma].append((pos, off))
            except KeyError:
                continue

        needed = {}
        for senses_list in lemma_senses.values():
            for pos, off in senses_list:
                needed.setdefault(pos, set()).add(off)

        for pos, offsets in needed.items():
            name = pos_files[pos]
            try:
                with tf.extractfile(tf.getmember(name)) as fh:
                    for raw in fh:
                        head = raw.decode('utf-8', 'replace')
                        off = head.split(' ', 1)[0]
                        if off not in offsets:
                            continue
                        want[(pos, off)] = set(w for w in lemmas_of(head) if w)
            except KeyError:
                continue

    mapping = {}
    for lemma, senses_list in lemma_senses.items():
        bucket = mapping.setdefault(lemma, set())
        for pos, off in senses_list:
            for other in want.get((pos, off), ()):
                if other.lower() != lemma:
                    bucket.add(other.lower())
    return mapping


def corpus_words(sidecars):
    """Every word used in this machine's prompts - the vocabulary that matters most here."""
    words = set()
    for j in Path(sidecars).glob('*.json'):
        try:
            prompt = json.loads(j.read_text(encoding='utf-8')).get('prompt') or ''
        except Exception:
            continue
        words.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]*", prompt))
    return words


def acceptable(word):
    return bool(re.fullmatch(r"[a-z][a-z' -]{1,20}", word)) and len(word.split()) <= 3


def build(mapping, vocabulary, rank=None):
    """Alternatives are restricted to words this dictionary would itself know.

    WordNet lists every sense of a headword, so "mug" offers fool/gull/patsy (the gullible-person sense) and "leaning"
    offers "atilt". Keeping only options that are common, in the corpus, or in the curated list turns that into
    prompt-shaped English: no synonym beats a wrong one. Ordering follows the frequency list, so the most usual
    alternative comes first.
    """
    rank = rank or {}
    out = {}
    for word in sorted(vocabulary):
        options = [o for o in mapping.get(word, ()) if acceptable(o) and o != word and o in VISUAL]
        # drop trivial plural/singular swaps: "bicycles" is not a rephrase of "bicycle"
        options = [o for o in options if not (o.rstrip('s') == word.rstrip('s') and o != word)]
        seen, kept = set(), []
        for o in sorted(options, key=lambda s: (rank.get(s, 10 ** 6), len(s.split()), len(s), s)):
            if o in seen:
                continue
            seen.add(o)
            kept.append(o)
            if len(kept) >= 6:
                break
        if kept:
            out[word] = kept
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--wordnet', default=str(Path(__import__('tempfile').gettempdir()) / 'WNdb-3.0.tar.gz'))
    ap.add_argument('--corpus', default=r'C:/Users/kevin/Pictures/zimage-ui',
                    help='folder of sidecars whose prompts supply vocabulary')
    ap.add_argument('--wordlist', default=str(Path(__import__('tempfile').gettempdir()) / 'common-words.txt'),
                    help='common-word list (top English by frequency) used to widen the vocabulary')
    ap.add_argument('--curated', default='rephrase.py', help='existing curated map, merged in as vocabulary')
    ap.add_argument('--senses', type=int, default=2, help='how many of each word\'s senses to use (0 = all)')
    ap.add_argument('--out', default='data/synonyms.json')
    args = ap.parse_args()

    tar = Path(args.wordnet)
    if not tar.exists():
        print('downloading WordNet 3.0 (~10 MB)...', flush=True)
        urllib.request.urlretrieve(WORDNET_URL, tar)  # noqa: S310 - fixed, well-known URL
    print('parsing WordNet...', flush=True)
    mapping = load_wordnet(tar, senses=(args.senses or 999))
    print('  %d headwords with synonyms' % len(mapping))

    vocabulary = set(COMMON)
    if Path(args.wordlist).exists():
        common = {w.strip().lower() for w in Path(args.wordlist).read_text(encoding='utf-8').split() if w.strip()}
        print('  %d common words available from the frequency list' % len(common))
        vocabulary |= common
    if Path(args.corpus).exists():
        corpus = corpus_words(args.corpus)
        print('  %d distinct words in the prompt corpus' % len(corpus))
        vocabulary |= corpus
    if Path(args.curated).exists():
        sys.path.insert(0, str(Path(args.curated).resolve().parent))
        try:
            import rephrase
            for key, options in rephrase.SYNONYMS.items():
                vocabulary.add(key)
                vocabulary.update(o.lower() for o in options)
            for options in rephrase.SYNONYMS.values():
                VISUAL.update(o.lower() for o in options)
        except Exception as e:
            print('  (could not read the curated map: %s)' % e)

    vocabulary = {w for w in vocabulary if acceptable(w)}
    rank = {}
    if Path(args.wordlist).exists():
        for i, w in enumerate(Path(args.wordlist).read_text(encoding='utf-8').split()):
            rank.setdefault(w.strip().lower(), i)
    table = build(mapping, vocabulary, rank)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {'source': 'WordNet 3.0 (Princeton University)',
               'license': 'data/WORDNET-LICENSE.txt',
               'created': datetime.now().isoformat(timespec='seconds'),
               'vocabulary': len(vocabulary),
               'entries': len(table),
               'synonyms': table}
    out.write_text(json.dumps(payload, indent=0, sort_keys=True), encoding='utf-8')
    size = out.stat().st_size
    print('\nwrote %s: %d entries from %d candidate words, %.0f KB'
          % (out, len(table), len(vocabulary), size / 1024))
    for probe in ('bicycle', 'weathered', 'sunset', 'mug', 'brick', 'glowing', 'leaning'):
        print('   %-10s %s' % (probe, table.get(probe)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
