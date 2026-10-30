"""Wins, ranks and sidecar bookkeeping for the paired A/B review.

The rule that decides the design: a win belongs to the *picture*, not to the folder or the run. Recategorising a
gallery, moving a picture between prompt folders, or restarting the app must not lose or reorder anyone's record, and a
picture that won in one folder has won - so it sorts first wherever it is shown next.

Nothing here deletes. `keep_both` / `delete_left` / `delete_right` already existed so the window could report an intent;
the new `left_wins` / `right_wins` / `tie` choices only ever add to a record.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pair_review          # the queue, the pair ids and the delete choices all live with the review session

# the durable half of a result, kept in the picture's own .json beside its prompt
META_KEYS = ('wins', 'losses', 'ties', 'bouts', 'last_bout')
WIN_CHOICES = ('left_wins', 'right_wins', 'tie')
DELETE_CHOICES = ('keep_both', 'delete_left', 'delete_right')
CHOICES = DELETE_CHOICES + WIN_CHOICES


def sidecar(path):
    """The .json that carries a picture's prompt - and now its record."""
    return Path(path).with_suffix('.json')


def read_meta(path):
    try:
        data = json.loads(sidecar(path).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_meta(path, meta):
    """Replace the sidecar atomically: a half-written .json would lose the prompt, which is the picture's identity."""
    target = sidecar(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix='.meta-', suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(meta, fh, indent=1)
        os.replace(tmp, target)
        return True
    except Exception:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def record(state, a, b, choice, when=None):
    """Apply one decision. Win choices update both sidecars and return None; delete choices return the path to delete.

    The record is symmetric and cumulative: a bout adds one to `wins` for the winner, `losses` for the loser, and
    `bouts` for both, so a picture compared ten times is not ranked the same as one compared twice.
    """
    if choice not in CHOICES:
        raise ValueError('unknown choice %r' % (choice,))
    if choice in DELETE_CHOICES:
        return pair_review.decide(state, a, b, choice)

    when = when or _now()
    if choice == 'left_wins':
        pairs = [(a, b)]
    elif choice == 'right_wins':
        pairs = [(b, a)]
    else:
        pairs = []
    for winner, loser in pairs:
        _bump(winner, win=True, when=when, opponent=loser)
        _bump(loser, win=False, when=when, opponent=winner)
    if choice == 'tie':
        _bump(a, win=None, when=when, opponent=b)
        _bump(b, win=None, when=when, opponent=a)
    # the pair stays remembered so a resumed run does not ask again
    state.setdefault('decisions', {})[pair_review._pair_id(a, b)] = {'choice': choice, 'left': Path(a).name,
                                                        'right': Path(b).name, 'at': when, 'mode': 'rank'}
    return None


def _bump(path, win, when, delta=1, opponent=None):
    meta = read_meta(path)
    if not meta:
        meta = {'image': str(path)}
    # clamped at zero: an undo after a counter was reset must not produce -1 and poison every later ranking
    meta['bouts'] = max(0, int(meta.get('bouts') or 0) + delta)
    if win is True:
        meta['wins'] = max(0, int(meta.get('wins') or 0) + delta)
    elif win is False:
        meta['losses'] = max(0, int(meta.get('losses') or 0) + delta)
    else:
        meta['ties'] = max(0, int(meta.get('ties') or 0) + delta)
    if opponent:
        # the per-opponent tally, by file name (unique inside a folder): what lets a Bradley-Terry fit exist at all
        vs = meta.get('vs') or {}
        if not isinstance(vs, dict):
            vs = {}
        key = Path(opponent).name
        row = list(vs.get(key) or [0, 0, 0])
        while len(row) < 3:
            row.append(0)
        if win is True:
            row[0] = max(0, row[0] + delta)
        elif win is False:
            row[1] = max(0, row[1] + delta)
        else:
            row[2] = max(0, row[2] + delta)
        vs[key] = row
        meta['vs'] = vs
    if delta > 0:
        meta['last_bout'] = when
    elif meta.get('last_bout') == when:
        meta.pop('last_bout', None)          # the bout it referred to no longer exists
    write_meta(path, meta)


def unrecord(state, a, b, when=None):
    """Undo the recorded decision for one pair: decrement the counters, forget the decision. Returns the choice undone.

    `record` is symmetric, so its inverse is too. Going back in a review has to leave the sidecars exactly as they were,
    or an "undo" would slowly inflate the win counts it is meant to retract.
    """
    guessed = (state.get('decisions') or {}).get(pair_review._pair_id(a, b))
    if not guessed:
        return None
    state['decisions'].pop(pair_review._pair_id(a, b), None)
    choice = guessed.get('choice')
    when = guessed.get('at') or when or _now()
    pairs = {'left_wins': [(a, b)], 'right_wins': [(b, a)]}.get(choice, [])
    for winner, loser in pairs:
        _bump(winner, win=True, when=when, delta=-1, opponent=loser)
        _bump(loser, win=False, when=when, delta=-1, opponent=winner)
    if choice == 'tie':
        _bump(a, win=None, when=when, delta=-1, opponent=b)
        _bump(b, win=None, when=when, delta=-1, opponent=a)
    return choice


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec='seconds')


def record_of(path):
    """This picture's standing: wins, losses, ties, bouts - zero for anything never compared."""
    meta = read_meta(path)
    return {k: int(meta.get(k) or 0) for k in ('wins', 'losses', 'ties', 'bouts')}


def rank_key(path):
    """Sort key: most wins first, then the better win rate, then fewer losses, then the newer file.

    Raw wins alone would let a picture that was compared twenty times outrank one that won every bout it entered, so the
    rate is the second term; losses before date stops a 3-5 from tying a 3-0.
    """
    r = record_of(path)
    decided = r['wins'] + r['losses']
    rate = (r['wins'] / decided) if decided else 0.0
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        mtime = 0.0
    return (-r['wins'], -rate, r['losses'], -mtime)


def bt_strength(paths, iterations=200, tol=1e-9):
    """Bradley-Terry strengths for a set of pictures, fitted from the per-opponent tallies in their sidecars.

    BT says P(i beats j) = s_i / (s_i + s_j); the MLE is fitted here by the standard MM update
        s_i <- W_i / sum_j n_ij / (s_i + s_j)
    which is monotone and needs no gradients. Strength is what makes "3 wins against the best" outrank "5 wins against
    pictures that always lose" - raw win counts cannot tell those apart, which is why the pane's order was a heuristic.

    Pictures with no bouts get the geometric mean (1.0) rather than 0, so they sort in the middle instead of sinking.
    Returns {str(path): strength}.
    """
    by_name = {Path(p).name: str(p) for p in paths}
    wins, matches = {}, {}
    for name, path in by_name.items():
        # read_meta, not record_of: the latter returns the four counters only and would hide the opponent tallies
        rec = read_meta(path) or {}
        wins.setdefault(name, 0)
        for opponent, row in (rec.get('vs') or {}).items():
            try:
                w, l, t = [int(x) for x in row[:3]]
            except Exception:
                continue
            wins[name] = wins.get(name, 0) + w
            rival = Path(str(opponent)).name          # tolerant: a hand-written tally may omit the extension
            if rival in by_name:
                matches[(name, rival)] = matches.get((name, rival), 0) + w + l + t
    strength = {name: 1.0 for name in by_name}
    if not matches:
        return {by_name[n]: 1.0 for n in by_name}
    for _ in range(iterations):
        before = dict(strength)
        for name in by_name:
            denom = 0.0
            for (a, b), n in matches.items():
                if a == name:
                    denom += n / (strength[name] + strength[b])
                elif b == name:
                    denom += n / (strength[name] + strength[a])
            if denom > 0:
                strength[name] = max(1e-9, wins.get(name, 0) / denom) if wins.get(name, 0) else 1e-9
        scale = sum(strength.values()) / max(1, len(strength))
        if scale:
            for name in strength:
                strength[name] /= scale
        if max(abs(strength[n] - before[n]) for n in strength) < tol:
            break
    return {by_name[n]: strength[n] for n in by_name}


def sort_by_strength(paths):
    """Rank order by fitted strength; raw wins and then recency break ties, so sparse data still behaves."""
    paths = [str(p) for p in paths]
    scores = bt_strength(paths)
    return sorted(paths, key=lambda p: (-scores.get(p, 1.0), rank_key(p)))


def sort_by_wins(paths):
    """The paths in rank order - this is what makes a winner first in whatever folder it is shown in."""
    return sorted(paths, key=rank_key)


def badge(path):
    """A short label for a tile: '4W', '4W 1L', or '' when the picture has no record."""
    r = record_of(path)
    if not r['bouts']:
        return ''
    out = '%dW' % r['wins'] if r['wins'] else ''
    if r['losses']:
        out += (' ' if out else '') + '%dL' % r['losses']
    if r['ties'] and not out:
        out = '%dT' % r['ties']
    return out


def reset_wins(paths):
    """Forget every record in a set of pictures - the explicit way to start a comparison from zero."""
    touched = 0
    for p in paths:
        meta = read_meta(p)
        if not meta:
            continue
        changed = False
        for k in META_KEYS:
            if k in meta:
                meta.pop(k)
                changed = True
        if changed and write_meta(p, meta):
            touched += 1
    return touched
