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
        _bump(winner, win=True, when=when)
        _bump(loser, win=False, when=when)
    if choice == 'tie':
        _bump(a, win=None, when=when)
        _bump(b, win=None, when=when)
    # the pair stays remembered so a resumed run does not ask again
    state.setdefault('decisions', {})[pair_review._pair_id(a, b)] = {'choice': choice, 'left': Path(a).name,
                                                        'right': Path(b).name, 'at': when, 'mode': 'rank'}
    return None


def _bump(path, win, when):
    meta = read_meta(path)
    if not meta:
        meta = {'image': str(path)}
    meta['bouts'] = int(meta.get('bouts') or 0) + 1
    if win is True:
        meta['wins'] = int(meta.get('wins') or 0) + 1
    elif win is False:
        meta['losses'] = int(meta.get('losses') or 0) + 1
    else:
        meta['ties'] = int(meta.get('ties') or 0) + 1
    meta['last_bout'] = when
    write_meta(path, meta)


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
