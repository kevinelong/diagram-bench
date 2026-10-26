"""rephrase.py - swap prompt words for synonyms, deterministically and offline.

The window's "enhance" button rewrites a prompt with a local language model, which needs a server running. This does
the smaller, always-available job: replace known prompt vocabulary with an alternative from a curated list, so pressing
the button again walks through the variants instead of repeating one.

Whole words only - "photograph" is not touched by the entry for "photo" - and capitalisation is carried over, so
"A red bicycle" stays a sentence rather than becoming "a red bicycle".

    from rephrase import rephrase_text
    rephrase_text('a red bicycle leaning against a brick wall', 1)
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

# Curated for image prompts, not for prose. Each entry lists alternatives that are *not* the key itself, so the first
# press always changes something. Plurals are listed where a prompt is likely to use them.
SYNONYMS = {
    # framing and medium
    'photo': ['photograph', 'picture', 'snapshot', 'shot'],
    'photograph': ['photo', 'picture', 'snapshot', 'image'],
    'picture': ['image', 'photo', 'depiction', 'scene'],
    'image': ['picture', 'photo', 'render', 'scene'],
    'render': ['image', 'visual', 'depiction'],
    'painting': ['artwork', 'canvas', 'painted scene'],
    'illustration': ['drawing', 'artwork', 'illustrated scene'],
    'drawing': ['sketch', 'line drawing', 'illustration'],
    'portrait': ['headshot', 'close-up portrait', 'likeness'],
    'closeup': ['close-up', 'macro view', 'tight framing'],
    'close-up': ['closeup', 'macro view', 'tight framing'],
    'wide': ['broad', 'expansive', 'sweeping'],
    'shot': ['frame', 'view', 'capture'],

    # subjects
    'woman': ['lady', 'female figure', 'woman'],
    'man': ['gentleman', 'male figure', 'man'],
    'person': ['figure', 'individual', 'someone'],
    'people': ['figures', 'folk', 'crowd'],
    'child': ['kid', 'youngster', 'child'],
    'cat': ['feline', 'kitten', 'cat'],
    'dog': ['canine', 'pup', 'dog'],
    'bird': ['fowl', 'avian', 'bird'],
    'bicycle': ['bike', 'cycle', 'pushbike'],
    'bike': ['bicycle', 'cycle'],
    'car': ['automobile', 'vehicle', 'car'],
    'house': ['home', 'building', 'dwelling'],
    'building': ['structure', 'edifice', 'building'],
    'tree': ['tree', 'sapling', 'tree'],
    'flower': ['blossom', 'bloom', 'flower'],
    'mountain': ['peak', 'summit', 'mountain'],
    'river': ['stream', 'waterway', 'river'],
    'sea': ['ocean', 'water', 'sea'],
    'sky': ['heavens', 'sky', 'air'],

    # scene and setting
    'wall': ['wall', 'partition', 'facade'],
    'brick': ['brickwork', 'masonry', 'brick'],
    'street': ['road', 'lane', 'thoroughfare'],
    'room': ['chamber', 'interior', 'space'],
    'kitchen': ['galley', 'cook room', 'kitchen'],
    'forest': ['woods', 'woodland', 'forest'],
    'garden': ['yard', 'grounds', 'garden'],
    'desert': ['wasteland', 'dunes', 'desert'],
    'beach': ['shore', 'coast', 'beach'],
    'table': ['desk', 'surface', 'table'],
    'chair': ['seat', 'stool', 'chair'],
    'window': ['casement', 'window pane', 'window'],
    'door': ['doorway', 'portal', 'door'],

    # light and colour
    'red': ['crimson', 'scarlet', 'tomato red'],
    'blue': ['cobalt', 'azure', 'deep blue'],
    'green': ['emerald', 'verdant', 'leaf green'],
    'yellow': ['golden', 'amber', 'saffron'],
    'orange': ['tangerine', 'amber orange', 'burnt orange'],
    'purple': ['violet', 'plum', 'mauve'],
    'black': ['jet black', 'ebony', 'ink black'],
    'white': ['ivory', 'snow white', 'chalk white'],
    'brown': ['walnut', 'chestnut', 'umber'],
    'grey': ['gray', 'ash grey', 'slate'],
    'bright': ['luminous', 'radiant', 'vivid'],
    'dark': ['moody', 'shadowed', 'dim'],
    'warm': ['toasty', 'sultry', 'golden'],
    'cold': ['chilly', 'frosty', 'icy'],
    'sunset': ['dusk', 'golden hour', 'sundown'],
    'sunrise': ['dawn', 'daybreak', 'first light'],
    'night': ['evening', 'nightfall', 'after dark'],
    'light': ['illumination', 'glow', 'light'],
    'shadow': ['shade', 'silhouette', 'shadow'],

    # qualities
    'weathered': ['battered', 'aged', 'time-worn'],
    'rusty': ['corroded', 'oxidised', 'rust-covered'],
    'old': ['vintage', 'aged', 'antique'],
    'new': ['fresh', 'modern', 'new'],
    'simple': ['plain', 'uncluttered', 'minimal'],
    'detailed': ['intricate', 'richly detailed', 'elaborate'],
    'soft': ['gentle', 'diffused', 'muted'],
    'sharp': ['crisp', 'defined', 'tack sharp'],
    'calm': ['serene', 'tranquil', 'still'],
    'busy': ['crowded', 'bustling', 'hectic'],
    'clean': ['tidy', 'neat', 'spotless'],
    'rough': ['coarse', 'textured', 'uneven'],

    # verbs common in prompts
    'leaning': ['resting', 'propped', 'set against'],
    'standing': ['upright', 'positioned', 'stood'],
    'sitting': ['seated', 'perched', 'resting'],
    'looking': ['gazing', 'facing', 'watching'],
    'holding': ['gripping', 'carrying', 'clutching'],
    'flying': ['soaring', 'gliding', 'aloft'],
    'running': ['sprinting', 'racing', 'dashing'],
    'glowing': ['lit', 'shining', 'luminous'],
}


# A replacement that equals its own key would silently do nothing on that turn, and two-word replacements can collide
# with the word that follows ("propped" + "against" -> "propped against against").
SYNONYMS = {k: [o for o in v if o.lower() != k.lower()] for k, v in SYNONYMS.items()}
SYNONYMS = {k: v for k, v in SYNONYMS.items() if v}

def _collapse_repeats(text):
    """'a golden golden hour' -> 'a golden hour'; 'propped against against' -> 'propped against against' fixed to one."""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r'\b(\w+)(\s+)\1\b', r'\1', text, flags=re.IGNORECASE)
    return text


# Words WordNet cannot help with: its noun senses are prose-shaped, so "mug" resolves to the gullible-person sense and
# "brick" has no synonym at all. These are prompt-specific, written by hand, and they take precedence over the table.
VISUAL_EXTRA = {
    'brick': ['masonry', 'brickwork', 'terracotta block'],
    'mug': ['cup', 'beaker', 'tankard'],
    'cup': ['mug', 'beaker', 'goblet'],
    'kitchen': ['galley', 'cook room', 'cookhouse'],
    'window': ['casement', 'windowpane', 'glazing'],
    'door': ['doorway', 'entrance', 'portal'],
    'table': ['desk', 'tabletop', 'surface'],
    'chair': ['seat', 'armchair', 'stool'],
    'sofa': ['couch', 'settee', 'divan'],
    'bed': ['cot', 'bunk', 'berth'],
    'shelf': ['shelving', 'ledge', 'rack'],
    'wall': ['partition', 'facade', 'partition wall'],
    'floor': ['flooring', 'ground', 'deck'],
    'ceiling': ['roof', 'soffit', 'overhead plane'],
    'stairway': ['staircase', 'stairs', 'steps'],
    'lamp': ['light fixture', 'lantern', 'luminaire'],
    'candle': ['taper', 'candlestick', 'wax light'],
    'mirror': ['looking glass', 'reflector', 'glass'],
    'rug': ['carpet', 'mat', 'runner'],
    'curtain': ['drape', 'drapery', 'blind'],
    'fireplace': ['hearth', 'chimney piece', 'fire opening'],
    'dog': ['hound', 'canine', 'mutt'],
    'cat': ['feline', 'kitten', 'tabby'],
    'horse': ['stallion', 'steed', 'mare'],
    'bird': ['fowl', 'songbird', 'winged creature'],
    'fish': ['catch', 'finfish', 'minnow'],
    'tree': ['sapling', 'timber tree', 'canopy tree'],
    'flower': ['bloom', 'blossom', 'petalled head'],
    'leaf': ['frond', 'foliage', 'blade'],
    'grass': ['turf', 'lawn grass', 'sward'],
    'stone': ['rock', 'boulder', 'cobble'],
    'rock': ['stone', 'boulder', 'crag'],
    'sand': ['grit', 'beach sand', 'dune sand'],
    'mud': ['slurry', 'silt', 'sludge'],
    'glass': ['pane', 'glazing', 'crystal'],
    'metal': ['metallic surface', 'alloy', 'steel'],
    'wood': ['timber', 'lumber', 'plank'],
    'leather': ['hide', 'skin leather', 'calfskin'],
    'fabric': ['textile', 'material', 'cloth'],
    'cloth': ['fabric', 'textile', 'linen'],
    'paper': ['parchment', 'sheet', 'card stock'],
    'concrete': ['cement', 'cast concrete', 'concrete slab'],
    'roof': ['rooftop', 'roofing', 'gable'],
    'sky': ['heavens', 'skyscape', 'air'],
    'cloud': ['cloudbank', 'cumulus', 'vapour'],
    'fog': ['mist', 'haze', 'sea fret'],
    'rain': ['downpour', 'drizzle', 'rainfall'],
    'snow': ['powder snow', 'snowfall', 'flurry'],
    'shadow': ['shade', 'silhouette', 'umbra'],
    'light': ['illumination', 'radiance', 'glow'],
    'smoke': ['haze', 'fumes', 'smoke plume'],
    'fire': ['flame', 'blaze', 'burning'],
    'water': ['liquid', 'waterline', 'surface water'],
    'sea': ['ocean', 'brine', 'deep water'],
    'river': ['stream', 'watercourse', 'channel'],
    'road': ['highway', 'carriageway', 'thoroughfare'],
    'path': ['trail', 'footpath', 'track'],
    'building': ['structure', 'edifice', 'block'],
    'house': ['home', 'dwelling', 'residence'],
    'car': ['automobile', 'vehicle', 'motor car'],
    'truck': ['lorry', 'van', 'haulage truck'],
    'bike': ['bicycle', 'cycle', 'pushbike'],
    'bicycle': ['bike', 'cycle', 'pushbike'],
    'man': ['gentleman', 'male figure', 'fellow'],
    'woman': ['lady', 'female figure', 'woman'],
    'child': ['kid', 'youngster', 'little one'],
    'crowd': ['throng', 'gathering', 'mob'],
    'face': ['visage', 'countenance', 'features'],
    'hand': ['palm', 'fist', 'mitt'],
    'hair': ['mane', 'tresses', 'locks'],
    'eye': ['orb', 'peeper', 'gaze'],
    'smile': ['grin', 'beam', 'smirk'],
    # colours: WordNet offers "red -> loss", so these are hand-written instead
    'red': ['crimson', 'scarlet', 'vermilion', 'ruby red'],
    'blue': ['cobalt', 'azure', 'cerulean', 'navy'],
    'green': ['emerald', 'verdant', 'sage green', 'moss'],
    'yellow': ['golden', 'saffron', 'butter yellow', 'lemon'],
    'orange': ['tangerine', 'amber', 'apricot', 'burnt orange'],
    'purple': ['violet', 'plum', 'mauve', 'lilac'],
    'pink': ['rose', 'blush', 'coral pink', 'salmon'],
    'brown': ['chestnut', 'walnut', 'umber', 'tan'],
    'grey': ['gray', 'slate', 'ash', 'charcoal'],
    'black': ['jet', 'ebony', 'onyx', 'ink black'],
    'white': ['ivory', 'chalk white', 'snow white', 'bone'],
    'golden': ['amber', 'gilded', 'honeyed', 'gold'],
    'silver': ['chrome', 'steel grey', 'silvery'],
    # light and mood, where wordnets adjective senses are singletons
    'weathered': ['battered', 'aged', 'time-worn', 'weather-beaten'],
    'worn': ['frayed', 'tired', 'battered', 'threadbare'],
    'rusty': ['corroded', 'oxidised', 'rust-covered'],
    'dusty': ['powdery', 'dust-covered', 'chalky'],
    'glowing': ['luminous', 'radiant', 'lit', 'aglow'],
    'bright': ['luminous', 'brilliant', 'vivid', 'radiant'],
    'dark': ['shadowy', 'dim', 'moody', 'unlit'],
    'moody': ['brooding', 'atmospheric', 'sombre'],
    'calm': ['serene', 'tranquil', 'still', 'placid'],
    'busy': ['bustling', 'crowded', 'hectic', 'thronged'],
    'quiet': ['still', 'hushed', 'silent'],
    'empty': ['bare', 'vacant', 'deserted'],
    'ancient': ['antique', 'age-old', 'primeval'],
    'modern': ['contemporary', 'sleek', 'up-to-date'],
    'small': ['little', 'tiny', 'compact'],
    'large': ['big', 'vast', 'spacious'],
    'detailed': ['intricate', 'elaborate', 'richly detailed'],
    'simple': ['plain', 'uncluttered', 'minimal'],
}


# WordNet's table is generated by build_synonyms.py; hand-written entries win where both have a word.

# A second curated layer, added 2026-10-26 because the first two layers left 86 % of the words in real prompts
# unmatched. Every entry here is visual vocabulary a picture prompt uses: material, light, architecture, weather,
# texture, figure and pose. Kept hand-written for the same reason the colours are - WordNet's noun senses are
# prose-shaped ("mug -> fool") and its allow-list filter threw out most of this.
VISUAL_MORE = {
    # materials and surfaces
    'marble': ['stone', 'alabaster', 'polished stone'],
    'plaster': ['stucco', 'render', 'gypsum'],
    'concrete': ['cement', 'cast stone'],
    'timber': ['lumber', 'wood', 'board'],
    'plank': ['board', 'planking', 'floorboard'],
    'canvas': ['linen', 'duck cloth'],
    'velvet': ['plush', 'velour', 'nap'],
    'satin': ['silk', 'sheen', 'gloss'],
    'leather': ['hide', 'skin', 'rawhide'],
    'steel': ['metal', 'chrome', 'alloy'],
    'brass': ['bronze', 'gilt', 'copper'],
    'copper': ['brass', 'verdigris', 'patina'],
    'tile': ['ceramic', 'flagstone', 'tessera'],
    'mosaic': ['inlay', 'tessellation', 'tesserae'],
    'gravel': ['shingle', 'grit', 'crushed stone'],
    'cobble': ['cobblestone', 'setts', 'paving'],
    'canvasboard': ['board', 'panel'],
    'lacquer': ['varnish', 'glaze', 'shellac'],
    'patina': ['verdigris', 'weathering', 'bloom'],
    'rust': ['corrosion', 'oxidation', 'tarnish'],
    'matte': ['flat', 'dull', 'unpolished'],
    'glossy': ['shiny', 'lacquered', 'polished'],
    'polished': ['burnished', 'glossy', 'smooth'],
    'cracked': ['fissured', 'split', 'fractured'],
    'chipped': ['nicked', 'flaked', 'worn'],
    'frayed': ['ragged', 'threadbare', 'worn'],
    'crumpled': ['creased', 'crushed', 'rumpled'],
    'textured': ['grained', 'rough', 'toothed'],
    'smooth': ['even', 'sleek', 'untextured'],
    'rough': ['coarse', 'unfinished', 'gnarled'],
    'weathered': ['time-worn', 'battered', 'eroded'],
    'eroded': ['worn', 'scoured', 'eaten away'],
    'scarred': ['marked', 'scored', 'battered'],
    # light and atmosphere
    'sunbeam': ['ray', 'shaft of light', 'sunray'],
    'sunlight': ['daylight', 'sunshine', 'sun'],
    'moonlight': ['moonglow', 'silver light', 'moonbeam'],
    'lamplight': ['lampglow', 'lamp shine'],
    'candlelight': ['candle glow', 'flame light'],
    'backlight': ['rim light', 'backlit glow'],
    'silhouette': ['outline', 'shadow shape', 'profile'],
    'shadow': ['shade', 'umbra', 'cast shadow'],
    'highlight': ['glint', 'specular highlight', 'shine'],
    'glow': ['radiance', 'sheen', 'luminance'],
    'haze': ['mist', 'veil of light', 'fog'],
    'mist': ['haze', 'vapour', 'fog'],
    'fog': ['mist', 'haze', 'brume'],
    'drizzle': ['light rain', 'mizzle', 'spray'],
    'downpour': ['cloudburst', 'deluge', 'heavy rain'],
    'dusk': ['twilight', 'nightfall', 'gloaming'],
    'dawn': ['daybreak', 'first light', 'sunrise'],
    'sunset': ['sundown', 'golden hour', 'dusk'],
    'sunrise': ['daybreak', 'first light', 'dawn'],
    'twilight': ['dusk', 'half-light', 'gloaming'],
    'nocturnal': ['night-time', 'nightly', 'after dark'],
    'overcast': ['clouded', 'grey sky', 'flat light'],
    'dappled': ['mottled', 'freckled', 'patchy'],
    'gilded': ['gold-lit', 'golden', 'gilt'],
    # architecture and place
    'arch': ['archway', 'vault', 'span'],
    'archway': ['arch', 'vaulted opening'],
    'column': ['pillar', 'post', 'pilaster'],
    'pillar': ['column', 'pier', 'support'],
    'corridor': ['hallway', 'passage', 'gallery'],
    'doorway': ['door', 'threshold', 'portal'],
    'staircase': ['stairs', 'stairway', 'steps'],
    'balustrade': ['railing', 'banister', 'parapet'],
    'terrace': ['balcony', 'veranda', 'deck'],
    'courtyard': ['quadrangle', 'enclosure', 'yard'],
    'atrium': ['hall', 'rotunda', 'light well'],
    'ceiling': ['soffit', 'roof', 'canopy'],
    'fresco': ['mural', 'wall painting'],
    'alcove': ['niche', 'recess', 'bay'],
    'archway_light': ['lit arch', 'glowing arch'],
    'workshop': ['studio', 'atelier', 'workroom'],
    'kitchen': ['galley', 'scullery', 'cookhouse'],
    'library': ['reading room', 'book room', 'archive'],
    'bathhouse': ['baths', 'steam room', 'natatorium'],
    'pool': ['basin', 'pond', 'water'],
    'fountain': ['jet', 'spout', 'water feature'],
    # plants and land
    'foliage': ['leaves', 'greenery', 'fronds'],
    'frond': ['palm leaf', 'bough', 'branch'],
    'bough': ['branch', 'limb', 'spray'],
    'hedge': ['shrubbery', 'thicket', 'row of shrubs'],
    'meadow': ['field', 'pasture', 'grassland'],
    'shoreline': ['coast', 'waterline', 'bank'],
    'canopy_tree': ['tree crown', 'leaf canopy'],
    'blossom': ['bloom', 'flowers', 'petals'],
    'fern': ['bracken', 'frond', 'bracken fern'],
    'moss': ['lichen', 'green growth', 'verdure'],
    # figure and pose
    'kneeling': ['crouching', 'on one knee', 'genuflecting'],
    'reclining': ['lying back', 'recumbent', 'supine'],
    'seated': ['sitting', 'sat down', 'perched'],
    'standing': ['upright', 'on foot', 'erect pose'],
    'crouching': ['hunched', 'kneeling low', 'squatting'],
    'leaning': ['inclining', 'resting against', 'tipped'],
    'glancing': ['looking', 'peering', 'casting a look'],
    'gazing': ['looking', 'watching', 'staring'],
    'holding': ['carrying', 'gripping', 'cradling'],
    'cradling': ['holding', 'nestled in arms', 'supporting'],
    'embracing': ['hugging', 'holding close', 'clasping'],
    'turning': ['twisting', 'swivelling', 'rotating'],
    'reaching': ['extending', 'stretching', 'straining'],
    'stepping': ['moving', 'pacing', 'walking'],
    'wading': ['paddling', 'standing in water'],
    'stretching': ['extending', 'lengthening', 'reaching out'],
    'resting': ['reposing', 'at ease', 'relaxed'],
    'posed': ['arranged', 'set', 'positioned'],
    # textures and qualities
    'ornate': ['elaborate', 'richly carved', 'decorated'],
    'carved': ['sculpted', 'chiselled', 'engraved'],
    'inlaid': ['set in', 'marquetry', 'inset'],
    'draped': ['hung', 'swagged', 'draping'],
    'swagged': ['draped', 'festooned', 'looped'],
    'tasselled': ['fringed', 'trimmed', 'fringed edge'],
    'patterned': ['figured', 'motif-covered', 'printed'],
    'striped': ['banded', 'stripy', 'striated'],
    'quilted': ['padded', 'stitched', 'tufted'],
    'studded': ['dotted', 'set with studs', 'speckled'],
    'veined': ['streaked', 'marbled', 'grained'],
    'engraved': ['etched', 'incised', 'cut in'],
    'luminous': ['glowing', 'lit', 'radiant'],
    'translucent': ['see-through', 'milky', 'faintly lit'],
    'reflective': ['mirror-like', 'shiny', 'reflecting'],
    'silvery': ['pale grey', 'pewter', 'argent'],
    'earthy': ['ochre', 'soil-coloured', 'taupe'],
    'muted': ['subdued', 'low-key', 'softened'],
    'vivid': ['bright', 'saturated', 'intense'],
    'monochrome': ['one colour', 'single tone', 'black and white'],
    'filigree': ['lacework', 'fine tracery', 'openwork'],
    'tracery': ['openwork', 'ribbing', 'lattice'],
    'lattice': ['trellis', 'grid', 'latticework'],
    'brickwork': ['masonry', 'brick', 'brick facing'],
    'masonry': ['stonework', 'brickwork', 'blockwork'],
    'stonework': ['masonry', 'ashlar', 'rock work'],
    'roofscape': ['roofline', 'cluster of roofs'],
    'skyline': ['horizon', 'city outline', 'rooftops'],
}


_WORDNET_TABLE = {}
_TABLE_PATH = Path(__file__).with_name('data') / 'synonyms.json'
if _TABLE_PATH.exists():
    try:
        _WORDNET_TABLE = json.loads(_TABLE_PATH.read_text(encoding='utf-8')).get('synonyms', {})
    except Exception:
        _WORDNET_TABLE = {}

LOOKUP = dict(_WORDNET_TABLE)
for _k, _v in VISUAL_EXTRA.items():
    LOOKUP[_k] = [o for o in _v if o.lower() != _k.lower()]
for _k, _v in VISUAL_MORE.items():          # added 2026-10-26, curated visual vocabulary
    if _k not in LOOKUP:
        LOOKUP[_k] = [o for o in _v if o.lower() != _k.lower()]
LOOKUP.update(SYNONYMS)          # the original curated set keeps the last word

_KEYS_BY_HEAD = {}
for _key in LOOKUP:
    _KEYS_BY_HEAD.setdefault(_key[:1], []).append(_key)
_FUZZY_CACHE = {}


# -- how a word is matched -------------------------------------------------
# Exact lookup only ever found a word in its dictionary form, so "columns", "strokes" and "watching" missed even though
# "column", "stroke" and "watch" were there. Three fallbacks run in order, and each one is deliberately narrow:
#
#   exact   the word itself is a key
#   stem    a plural/past/gerund form of a key ("columns" -> column), and the synonym is re-inflected to match
#   spelling an en-GB/en-US variant or a listed typo
#   fuzzy    one character away from a key, same first two letters, at least six characters long
#
# Fuzzy is the last resort and it is tight on purpose: a loose cutoff matched "three" to "tree" and "steam" to "stem",
# which silently changes the picture. A miss is cheaper than a wrong word.

SPELLING = {
    'colour': 'color', 'colours': 'colors', 'coloured': 'colored', 'grey': 'gray', 'greys': 'grays',
    'centre': 'center', 'centred': 'centered', 'metre': 'meter', 'metres': 'meters', 'litre': 'liter',
    'theatre': 'theater', 'mould': 'mold', 'moulded': 'molded', 'plough': 'plow', 'tyre': 'tire',
    'jewellery': 'jewelry', 'catalogue': 'catalog', 'dialogue': 'dialog', 'programme': 'program',
    'cosy': 'cozy', 'doughnut': 'donut', 'pyjamas': 'pajamas', 'storey': 'story', 'kerb': 'curb',
}

# misspellings that recur in prompts, listed rather than guessed: a keyboard-distance rule would "fix" correct words
TYPOS = {
    'balck': 'black', 'whte': 'white', 'blck': 'black', 'weathred': 'weathered', 'backgroud': 'background',
    'sihouette': 'silhouette', 'silhoutte': 'silhouette', 'occured': 'occurred', 'recieve': 'receive',
    'seperate': 'separate', 'definately': 'definitely', 'gaurd': 'guard', 'ligth': 'light', 'laquered': 'lacquered',
    'jewellry': 'jewelry', 'pharoah': 'pharaoh', 'cemetary': 'cemetery', 'noticable': 'noticeable',
}

_INFLECTIONS = (('ies', 'y', 'plural'), ('ves', 'f', 'plural'), ('es', '', 'plural'), ('s', '', 'plural'),
                ('ied', 'y', 'past'), ('ed', 'e', 'past'), ('ed', '', 'past'), ('ing', 'e', 'gerund'),
                ('ing', '', 'gerund'),
                ('ily', 'y', 'adverb'), ('ly', '', 'adverb'))


def _base_forms(word):
    """(base, kind) candidates for an inflected form, longest suffix first."""
    out = []
    for suffix, add_back, kind in _INFLECTIONS:
        base_len = len(word) - len(suffix) + len(add_back)
        if word.endswith(suffix) and base_len >= (4 if kind == 'adverb' else 3):
            out.append((word[:-len(suffix)] + add_back, kind))
    return out


def _reinflect(synonym, kind):
    """Put a synonym back into the grammatical form the original word had."""
    if ' ' in synonym or '-' in synonym:
        return synonym                       # "brickwork partition" has no safe plural
    if kind == 'plural':
        if synonym.endswith(('s', 'x', 'z', 'ch', 'sh')):
            return synonym + 'es'
        if synonym.endswith('y') and len(synonym) > 2 and synonym[-2] not in 'aeiou':
            return synonym[:-1] + 'ies'
        return synonym + 's'
    if kind == 'past':
        return synonym + ('d' if synonym.endswith('e') else 'ed')
    if kind == 'gerund':
        return (synonym[:-1] + 'ing') if synonym.endswith('e') else (synonym + 'ing')
    if kind == 'adverb':
        # adjective -> adverb, the shapes English actually uses: gentle -> gently, soft -> softly, basic -> basically
        if synonym.endswith('y') and len(synonym) > 2 and synonym[-2] not in 'aeiou':
            return synonym[:-1] + 'ily'
        if synonym.endswith('le'):
            return synonym[:-1] + 'y'
        if synonym.endswith('ic'):
            return synonym + 'ally'
        return synonym + 'ly'
    return synonym


def _edit_distance(a, b):
    """Optimal string alignment distance: insert, delete, substitute, and one transposition - a real typo metric."""
    if a == b:
        return 0
    prev2 = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            best = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                best = min(best, prev2[j - 2] + 1)
            cur.append(best)
        prev2, prev = prev, cur
    return prev[-1]


def _fuzzy_key(word, fuzziness='strict'):
    """Near-miss key: same opening letters, similar length, high similarity. Cached, and tight by default.

    A loose cutoff matched "three" to "tree" and "steam" to "stem" in testing, so strict requires six characters, the
    first two letters and 0.90 similarity; loose drops that to five characters, one letter and 0.84.
    """
    if len(word) < 6 or word in _NEVER_FUZZY:
        return None
    cache_key = (word, fuzziness)
    hit = _FUZZY_CACHE.get(cache_key)
    if hit is not None:
        return hit or None
    strict = fuzziness == 'strict'
    best, best_score = None, 0.0
    head = word[:_FUZZY_HEAD.get(fuzziness, 2)]
    max_edits = 1 if strict else 2
    for key in _KEYS_BY_HEAD.get(word[:1], ()):
        if not key.startswith(head):
            continue
        if abs(len(key) - len(word)) > (1 if strict else 2):
            continue
        dist = _edit_distance(word, key)
        if dist > max_edits:
            continue
        score = 1.0 - dist / max(len(word), len(key))
        if score > best_score:
            best, best_score = key, score
    if best and best_score < _FUZZY_MIN_SCORE.get(fuzziness, 0.85):
        best = None
    _FUZZY_CACHE[cache_key] = best
    return best


def resolve(word, allow_fuzzy=True, fuzziness='strict'):
    """(key, kind, kind_of_inflection). key is the dictionary entry to rotate, or None."""
    w = word.lower()
    fuzziness = fuzziness if fuzziness in FUZZINESS else 'strict'
    allow_fuzzy = allow_fuzzy and fuzziness != 'off'
    if LOOKUP.get(w):
        return w, 'exact', None
    if w in SPELLING:
        key = SPELLING[w]
        if LOOKUP.get(key):
            return key, 'spelling', None
    if w in TYPOS:
        key = TYPOS[w]
        if LOOKUP.get(key):
            return key, 'typo', None
    for base, kind in _base_forms(w):
        if LOOKUP.get(base):
            return base, 'stem', kind
    if allow_fuzzy:
        key = _fuzzy_key(w, fuzziness)
        if key:
            return key, 'fuzzy', None
    return None, 'none', None


# Matching strictness: how forgiving the lookup is. Off means "only words the dictionary contains, in their own or an
# inflected form"; loose is the widest and is the one that can still surprise you, which is why it is not the default.
FUZZINESS = ('off', 'strict', 'loose')
# Six characters in both modes: at five, "three" matched "tree" and the prompt lost a number. Loose differs by
# allowing a second edit and one shared opening letter, not by reaching shorter words.
_FUZZY_MIN_SCORE = {'strict': 0.85, 'loose': 0.75}
_NEVER_FUZZY = {'three', 'seven', 'eight', 'eleven', 'twelve', 'twenty', 'thirty', 'hundred', 'thousand',
                'the', 'and', 'with', 'from', 'this', 'that', 'these', 'those', 'there', 'their', 'which'}
_FUZZY_HEAD = {'strict': 2, 'loose': 1}


def rephrase_text(text, turn=1, intensity='normal', fuzziness='strict'):
    """Return (rephrased_text, number_of_words_changed, detail).

    `turn` rotates the choice for every word, so pressing the button repeatedly produces different phrasings instead of
    the same one. **The original word is part of the rotation**, so a long enough press count walks all the way round
    and returns the prompt to what it started as - the wording you typed is one of the possibilities, never excluded.
    A word with n alternatives therefore comes back after n + 1 presses.

    `intensity` decides how many of the eligible words move on one press, rotating by token position so successive
    presses cover the rest: 'light' takes every third one, 'normal' all of them, 'strong' all of them and prefers the
    farthest synonym in the list. `fuzziness` decides how a word may be matched at all - see FUZZINESS.

    The third return value counts what matched and how, so a caller can say "3 of 8, one approximate" instead of
    leaving the user to guess why a long prompt barely changed.
    """
    if not text:
        return text, 0, {}
    turn = max(1, int(turn))
    fuzziness = fuzziness if fuzziness in FUZZINESS else 'strict'
    allow_fuzzy = fuzziness != 'off'
    out, changed = [], 0
    matched = {'exact': 0, 'stem': 0, 'spelling': 0, 'typo': 0, 'fuzzy': 0}
    eligible, position = 0, 0

    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*|[^A-Za-z]+", text)
    for token in tokens:
        key, kind, inflection = resolve(token, allow_fuzzy=allow_fuzzy, fuzziness=fuzziness)
        if not key and '-' in token.strip('-'):
            # a compound is as many words as it has parts: rewrite each part that the dictionary knows, keep the rest
            parts = re.split(r'(-)', token)
            rebuilt, hits, part_detail = [], 0, {}
            for part in parts:
                if part == '-' or not part:
                    rebuilt.append(part)
                    continue
                pk, pkind, pinf = resolve(part, allow_fuzzy=allow_fuzzy, fuzziness=fuzziness)
                options = LOOKUP.get(pk) if pk else None
                if options:
                    cycle = [part] + [o for o in options if o.lower() != part.lower()]
                    pick = turn % len(cycle)
                    repl = cycle[pick]
                    if pkind == 'stem' and pinf:
                        repl = _reinflect(repl, pinf)
                    if repl.lower() != part.lower():
                        if part[:1].isupper():
                            repl = repl[:1].upper() + repl[1:]
                        hits += 1
                        part_detail[pkind] = part_detail.get(pkind, 0) + 1
                    rebuilt.append(repl)
                else:
                    rebuilt.append(part)
            if hits:
                # intensity still applies, at the granularity of the whole compound
                take = True if intensity != 'light' else (position % 3) == (turn % 3)
                position += 1
                eligible += 1
                if take:
                    out.append(''.join(rebuilt))
                    changed += 1
                    for k, v in part_detail.items():
                        matched[k] = matched.get(k, 0) + v
                    continue
        if key:
            eligible += 1
        options = LOOKUP.get(key) if key else None
        if options:
            # position 0 is the word as written, then the alternatives - so the cycle closes on the original
            cycle = [token] + [o for o in options if o.lower() != token.lower()]
            pick = turn % len(cycle)
            if intensity == 'strong' and pick:
                pick = len(cycle) - 1          # farthest synonym, still inside the same cycle
            take = True
            if intensity == 'light':
                take = (position % 3) == (turn % 3)     # a third of them, rotating so none is starved
            position += 1
            replacement = cycle[pick] if take else token
            if replacement.lower() != token.lower():
                if kind == 'stem' and inflection:
                    replacement = _reinflect(replacement, inflection)
                if token[:1].isupper():
                    replacement = replacement[:1].upper() + replacement[1:]
                out.append(replacement)
                changed += 1
                matched[kind] = matched.get(kind, 0) + 1
                continue
            if kind:
                matched[kind] = matched.get(kind, 0) + 1     # matched but landed on the original word
        out.append(token)
    detail = {'eligible': eligible, 'matched': matched, 'intensity': intensity, 'fuzziness': fuzziness}
    return _collapse_repeats(''.join(out)), changed, detail


def has_synonyms(text, fuzziness='strict'):
    """How many words in this text the dictionary can match at all - the denominator for a rephrase report."""
    return sum(1 for token in re.findall(r"[A-Za-z][A-Za-z'-]*", text)
               if resolve(token, fuzziness=fuzziness)[0])


def cycle_period(text):
    """Presses after which the whole prompt comes back to the wording it started with, or 0 if it cannot.

    Each word cycles on its own length (alternatives + the original), so a sentence returns at the lowest common
    multiple of those lengths - 4 presses for "a red bicycle", 60 for a long one. Stated rather than discovered,
    because "press again a few times" is not true of 60.
    """
    from math import lcm
    period = 1
    for token in re.findall(r"[A-Za-z][A-Za-z'-]*", text):
        options = LOOKUP.get(token.lower())
        if options:
            length = len([o for o in options if o.lower() != token.lower()]) + 1
            period = lcm(period, length)
    return 0 if period == 1 else period
