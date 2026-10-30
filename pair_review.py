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


# A small set is worth comparing exhaustively: every pair once.
#
#   n   all pairs   rounds (~n/2 per round)
#   4        6          4
#   5       10          6
#   6       15          9
#   7       21          9      <- rounds start saving more than half
#
# The count is not the whole argument: at 15 questions or fewer the cost is a couple of minutes of clicking, and the
# complete graph is strictly better data than a schedule - every picture has met every other, so the Bradley-Terry fit is
# fully determined, contradictions ("A beat B, B beat C, C beat A") are visible at all, and each picture's record is
# perfectly balanced. Above that, rounds cost less for an order that is nearly as good.
EXHAUSTIVE_MAX = 6

# A decision costs about three seconds of a person's attention (measured on this window, 2026-10-29), so a plan can be
# priced in minutes before it starts: 15 questions is under a minute, 144 is about seven.
DECISION_SECONDS = 3.0


def estimate_minutes(pairs, per_decision=DECISION_SECONDS):
    """Rough wall-clock for a plan, from the measured pace."""
    return len(pairs) * per_decision / 60.0


def _session_order(state):
    """The order the decisions currently imply (wins, then losses, then name) - cheap, and enough to notice no change."""
    scores = _session_record(state)
    names = sorted({Path(p).name for p in state.get('paths') or []})
    return tuple(sorted(names, key=lambda n: (-scores.get(n, (0, 0, 0))[0], scores.get(n, (0, 0, 0))[1], n)))


def rounds_for(count):
    """How many rounds a full order needs: log2(n), which is what a comparison sort costs, and at least two."""
    import math
    return max(2, int(math.ceil(math.log2(max(2, count)))))


def _session_record(state):
    """{name: (wins, losses, ties)} from the decisions of *this* review, not from the lifetime sidecars."""
    scores = {}
    for pair_id, decided in (state.get('decisions') or {}).items():
        left, right = decided.get('left'), decided.get('right')
        choice = decided.get('choice')
        for name in (left, right):
            scores.setdefault(name, [0, 0, 0])
        if choice == 'left_wins':
            scores[left][0] += 1
            scores[right][1] += 1
        elif choice == 'right_wins':
            scores[right][0] += 1
            scores[left][1] += 1
        elif choice == 'tie':
            scores[left][2] += 1
            scores[right][2] += 1
    return {k: tuple(v) for k, v in scores.items()}


def next_round(state, hash_map=None):
    """The next round of pairs for a *ranking* review: equal records, never a rematch.

    Round one is the most-similar matching (`pair_up`), which is what makes the first questions easy. After that the
    pairing is by record - winners with winners, losers with losers - which is the Swiss idea: k rounds of this separate
    the field, and log2(n) rounds are enough for a total order instead of the n^2 of every pair.

    Returns the pairs appended to `state['pairs']` (and extends it), or [] when the schedule is finished.
    """
    paths = state.get('paths') or []
    if not paths:
        return []
    target = int(state.get('target_rounds') or rounds_for(len(paths)))
    done_round = int(state.get('round') or 1)
    if done_round >= target:
        return []
    # stop as soon as a round would not move anybody: at three seconds a question, a round that changes nothing is
    # three minutes of someone's evening
    order = _session_order(state)
    previous = state.get('last_order')
    if previous and order == tuple(previous):
        state['settled'] = True
        return []
    state['last_order'] = list(order)
    decided = set((state.get('decisions') or {}).keys())
    scores = _session_record(state)
    by_name = {Path(p).name: str(p) for p in paths}
    # bucket by record; a missing record means "not yet compared", which belongs with the middle of the field
    buckets = {}
    for name, path in by_name.items():
        buckets.setdefault(scores.get(name, (0, 0, 0)), []).append(str(path))
    ordered_keys = sorted(buckets, key=lambda k: (-k[0], k[1], k[2]))
    queue = []                                   # the field, bucket by bucket, strongest record first
    for key in ordered_keys:
        queue.extend(sorted(buckets[key], key=lambda p: Path(p).name))
    taken, pairs = set(), []
    while len(queue) > 1:
        a = queue.pop(0)
        if a in taken:
            continue
        partner = None
        # the nearest-bucketed opponent that has not met a, else the next available picture
        for b in queue:
            if b in taken or b == a:
                continue
            if _pair_id(a, b) in decided:
                continue
            partner = b
            break
        if partner is None:
            for b in queue:
                if b not in taken and b != a:
                    partner = b          # a rematch beats dropping a picture from the round
                    break
        if partner is None:
            continue
        queue.remove(partner)
        taken.update((a, partner))
        pairs.append((a, partner, 0))
    if not pairs:
        return []
    state.setdefault('pairs', []).extend([[a, b, d] for a, b, d in pairs])
    state['round'] = done_round + 1
    return pairs


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
    state.setdefault('paths', [str(p) for p in paths])
    state.setdefault('target_rounds', rounds_for(len(paths)))
    state.setdefault('round', 1)
    if len(paths) <= EXHAUSTIVE_MAX:
        # every pair once, most similar first (the distance is only used to order the questions, not to filter)
        got = hashes(paths, store=hash_store)
        str_paths = [str(p) for p in paths]
        scored = []
        for i in range(len(str_paths)):
            for j in range(i + 1, len(str_paths)):
                a, b = str_paths[i], str_paths[j]
                ha, hb = got.get(a), got.get(b)
                dist = hamming(ha, hb) if (ha and hb) else 999
                scored.append((dist, a, b))
        scored.sort(key=lambda row: (row[0], row[1], row[2]))
        state['pairs'] = [[a, b, d] for d, a, b in scored]
        state['leftovers'] = []
        state['exhaustive'] = True
        state['round'] = 1
        state['target_rounds'] = 1
        state['total'] = len(state['pairs'])
        return state
    got = hashes(paths, store=hash_store)
    pairs, leftovers = pair_up(paths, got)
    state['pairs'] = [[a, b, d] for a, b, d in pairs]
    state['leftovers'] = leftovers
    state['exhaustive'] = False
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
