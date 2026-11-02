"""estimate.py - what a run will cost, from what was actually measured on that card.

`PLAN-UX-20261102.md` §4.2 and §5 need one number the app can show *before* it renders: seconds per picture. The window
used a flat 20 s placeholder (`_seconds_per_picture_value or 20.0`), which is wrong by 10x in both directions - 14.3 s on
this desktop for z-image turbo, 142.5 s on the 4060 laptop for the same job.

So this reads the measured table (`deployments.RUNS`) instead of guessing, and returns **None** when nothing was measured:
the dialog then says "cost unknown on this machine" and shows the count, which is more useful than an invented number.

Matching rule: the exact (card, profile, size) must have been measured. `steps` is then applied as a linear multiplier,
because the measured rows carry the step count they were timed at - that one is arithmetic on a real measurement, not a
model of the sampler. Anything else returns None on purpose; the 4060 box's own numbers (`MACHINES-2026-11-02.md` §7)
are what fill these rows in.
"""
from __future__ import annotations

import deployments


def _rows():
    return [r for r in getattr(deployments, 'RUNS', []) if r.get('fits') and r.get('wall_s')]


def wall_seconds(card, profile, size, steps=None):
    """Seconds for one picture of `profile` at `size` on `card`, or None when it was never measured."""
    rows = _rows()
    if not rows or not card:
        return None
    same_card = [r for r in rows if r.get('card') == card]
    if not same_card:
        return None
    exact = [r for r in same_card if r.get('profile') == profile and max(r.get('width') or 0,
                                                                         r.get('height') or 0) == size]
    if exact:
        row = min(exact, key=lambda r: r['wall_s'])
        return _scale_steps(row, steps)
    # No extrapolation to another size. Scaling by pixels looked reasonable and produced 437 s for flux3 klein 4b at
    # 1024 px from a 512 px measurement - a guess wearing a measurement's clothes, and precisely what the dialog must not
    # show. Measured at this size or unknown; the count is still worth printing.
    return None


def _scale_steps(row, steps):
    """A measured row carries its own step count; a run at another count scales roughly linearly in the sampler."""
    wall = float(row['wall_s'])
    measured = row.get('steps')
    if not steps or not measured:
        return wall
    return wall * (float(steps) / float(measured))


def fastest_seconds(card, size):
    """The fastest measured picture at this size on this card - what the hunt warning compares the active model against."""
    rows = [r for r in _rows() if r.get('card') == card
            and max(r.get('width') or 0, r.get('height') or 0) == size]
    if not rows:
        return None, None
    row = min(rows, key=lambda r: r['wall_s'])
    return row['wall_s'], row.get('profile')


def total(n, per_picture):
    """Total seconds for `n` renders, or None when the per-picture cost is unknown."""
    if per_picture is None:
        return None
    return max(0, int(n)) * float(per_picture)


def hunt_advice(card, profile, size, steps, n, threshold_s=600.0, largest_size=None):
    """Whether a matrix is worth a warning, and why - the rule from PLAN-UX-20261102.md §5 as data.

    All three must hold: the size is the profile's largest, the active model is not the fastest measured for this card at
    that size, and the *extra* estimated work exceeds the threshold. Returns a dict the dialog can render, never a bool,
    because the dialog has to explain itself with numbers.
    """
    out = {'warn': False, 'n': n, 'per_picture': None, 'total': None, 'fast': None, 'fast_profile': None,
           'fast_total': None, 'extra': None, 'reason': ''}
    per = wall_seconds(card, profile, size, steps)
    out['per_picture'] = per
    out['total'] = total(n, per)
    fast, fast_profile = fastest_seconds(card, size)
    out['fast'], out['fast_profile'] = fast, fast_profile
    if fast is not None:
        out['fast_total'] = total(n, fast)
    if largest_size is not None and size < largest_size:
        out['reason'] = 'not the largest size'
        return out
    if per is None:
        out['reason'] = 'cost unknown on this machine'
        return out
    if fast is None or fast_profile == profile:
        out['reason'] = 'already the fastest measured at this size'
        return out
    extra = (per - fast) * max(0, int(n))
    out['extra'] = extra
    if extra <= threshold_s:
        out['reason'] = 'within the %s budget' % ('%.0f min' % (threshold_s / 60.0))
        return out
    out['warn'] = True
    out['reason'] = 'estimated %s slower than %s at this size' % (
        _dur(extra), fast_profile)
    return out


def _dur(seconds):
    if seconds is None:
        return 'unknown'
    seconds = float(seconds)
    if seconds < 60:
        return '%.0f s' % seconds
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return '%d min %d s' % (minutes, rest)
    hours, minutes = divmod(minutes, 60)
    return '%d h %d min' % (hours, minutes)
