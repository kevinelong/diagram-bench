"""pair_review.py - pair up near-identical pictures and keep score while a human picks one.

The job: inside one prompt folder there are often dozens of takes of the same scene. The only useful order to look at
them is "most alike first", because those are the ones where a choice actually has to be made. So this module

  1. hashes each picture perceptually (dHash on a 9x8 greyscale: 64 bits, cheap, no GPU, works from the thumbnail cache),
  2. pairs them greedily by Hamming distance, most similar pair first, never using a picture twice,
  3. and remembers every decision in a JSON state file outside the gallery, so a 340-pair set survives being closed.

Decisions are `keep_both`, `delete_left`, `delete_right`. The module never deletes anything itself - it records the
decision and hands the caller the path, because deletion goes through the window's Recycle Bin path.

Nothing here reads a prompt and nothing reads the model: this is a *visual* pairing on purpose, to complement the
prompt grouping in group_prompts.py.
"""
import hashlib
import json
import time
from pathlib import Path

STATE_DIR = Path.home() / '.local' / 'zimage-ui' / 'review'
HASH_SIZE = 8                      # 8x8 bits


def dhash(path, size=HASH_SIZE):
    """Perceptual difference hash as hex, from a thumbnail if one exists (falls back to the full picture)."""
    from PIL import Image
    src = Path(path)
    thumb = src.parent / '.thumbs' / (src.stem + '.jpg')
    with Image.open(thumb if thumb.exists() else src) as im:
        small = im.convert('L').resize((size + 1, size), Image.LANCZOS)
    pix = list(small.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = pix[row * (size + 1) + col]
            right = pix[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return '%016x' % bits


def hamming(a, b):
    """Bit distance between two hex hashes."""
    return bin(int(a, 16) ^ int(b, 16)).count('1')


def hashes(paths, cache=None, store=None):
    """dHash for each path, cached in `cache` (a dict) and persisted to `store` (a JSON path) when given."""
    known = dict(cache) if cache else {}
    if store and Path(store).exists():
        try:
            known.update(json.loads(Path(store).read_text(encoding='utf-8')))
        except Exception:
            pass
    out, changed = {}, False
    for p in paths:
        key = str(p)
        if key in known:
            out[key] = known[key]
            continue
        try:
            out[key] = dhash(p)
            changed = True
        except Exception:
            out[key] = None
    if store and changed:
        try:
            Path(store).parent.mkdir(parents=True, exist_ok=True)
            Path(store).write_text(json.dumps(out, indent=0), encoding='utf-8')
        except Exception:
            pass
    return out


def pair_up(paths, hash_map):
    """Greedy disjoint pairing, most similar first. Returns [(a, b, distance)] with a,b as str paths."""
    items = [(str(p), hash_map.get(str(p))) for p in paths]
    items = [(p, h) for p, h in items if h]
    scored = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            scored.append((hamming(items[i][1], items[j][1]), items[i][0], items[j][0]))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    used, pairs = set(), []
    for dist, a, b in scored:
        if a in used or b in used:
            continue
        used.add(a)
        used.add(b)
        pairs.append((a, b, dist))
    leftovers = [p for p, h in items if p not in used]
    return pairs, leftovers


def state_key(paths):
    """A stable id for "this folder's review session": the hash of its sorted member names."""
    names = '\n'.join(sorted(Path(p).name for p in paths))
    return hashlib.sha1(names.encode('utf-8')).hexdigest()[:16]


def load_state(key, state_dir=None):
    path = Path(state_dir or STATE_DIR) / ('%s.json' % key)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'key': key, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'decisions': {}, 'pairs': None}


def save_state(state, state_dir=None):
    path = Path(state_dir or STATE_DIR) / ('%s.json' % state['key'])
    path.parent.mkdir(parents=True, exist_ok=True)
    state['updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    path.write_text(json.dumps(state, indent=1), encoding='utf-8')
    return path


def build(paths, state, hash_store=None):
    """Fill state['pairs'] from a picture list, honouring decisions already recorded. Idempotent."""
    if state.get('pairs') is not None:
        return state
    got = hashes(paths, store=hash_store)
    pairs, leftovers = pair_up(paths, got)
    state['pairs'] = [[a, b, d] for a, b, d in pairs]
    state['leftovers'] = leftovers
    state['total'] = len(pairs)
    return state


def remaining(state):
    """The pairs still undecided, in order."""
    done = set(state.get('decisions') or {})
    out = []
    for a, b, d in state.get('pairs') or []:
        if _pair_id(a, b) in done:
            continue
        out.append((a, b, d))
    return out


def _pair_id(a, b):
    return '|'.join(sorted((Path(a).name, Path(b).name)))


def decide(state, a, b, choice):
    """Record one decision. choice in {'keep_both','delete_left','delete_right'}. Returns the path to delete, if any."""
    if choice not in ('keep_both', 'delete_left', 'delete_right'):
        raise ValueError('unknown choice %r' % (choice,))
    state.setdefault('decisions', {})[_pair_id(a, b)] = {
        'choice': choice, 'left': Path(a).name, 'right': Path(b).name,
        'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    if choice == 'delete_left':
        return a
    if choice == 'delete_right':
        return b
    return None


def progress(state):
    """(decided, total, deletes) for a status line."""
    decisions = state.get('decisions') or {}
    deletes = sum(1 for d in decisions.values() if d.get('choice', '').startswith('delete'))
    return len(decisions), state.get('total') or len(state.get('pairs') or []), deletes
