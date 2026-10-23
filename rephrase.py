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
LOOKUP.update(SYNONYMS)          # the original curated set keeps the last word


def rephrase_text(text, turn=1):
    """Return (rephrased_text, number_of_words_changed).

    `turn` rotates the choice for every word, so pressing the button repeatedly produces different phrasings instead of
    the same one. Words with no entry, punctuation and spacing are preserved exactly.
    """
    if not text:
        return text, 0
    turn = max(1, int(turn))
    out, changed = [], 0
    for token in re.findall(r"[A-Za-z][A-Za-z'-]*|[^A-Za-z]+", text):
        options = LOOKUP.get(token.lower())
        if options:
            replacement = options[(turn - 1) % len(options)]
            if replacement.lower() != token.lower():
                if token[:1].isupper():
                    replacement = replacement[:1].upper() + replacement[1:]
                out.append(replacement)
                changed += 1
                continue
        out.append(token)
    return _collapse_repeats(''.join(out)), changed
