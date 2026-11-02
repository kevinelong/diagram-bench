"""matrix.py - the arithmetic a strength matrix needs, kept away from the window.

`PLAN-UX-20261102.md` §4.1: the dialog that replaces the five inline widgets (`from` / `to` / `×n` / `matrix` / base
buttons) is a thin editor over this module, so the parts that can be wrong - the window at the ends of the range, the
number of renders, the packing of a contact sheet - are testable without a Tk window or a GPU.

Two decisions carried in from the plan, both from measurement rather than taste:

* **shift the window, never truncate it.** A strength of 95 % must open at 80-100 (21 values), not 85-100 (16): a short
  window at the top of the range reads as a bug to the person who just clicked.
* **step 1 by default.** Hunting for the right strength is a one-unit sweep; the dialog shows the count and the measured
  cost, which is what makes a 21-render default honest instead of reckless.
"""
from __future__ import annotations

import math

MIN_STRENGTH = 0
MAX_STRENGTH = 100
SPAN = 20           # the default window is current -10 .. current +10
COARSE_STEP = 2     # the preset that halves the count


def _clamp(value, low=MIN_STRENGTH, high=MAX_STRENGTH):
    return max(low, min(high, int(round(value))))


def window(current, span=SPAN, low=MIN_STRENGTH, high=MAX_STRENGTH):
    """(from, to) around `current`: +-span/2, shifted rather than truncated when it meets a wall.

    A legal range narrower than the span uses the whole range - the case where a model caps strength tightly, and where
    a truncated window would silently offer fewer values than asked for.
    """
    if high - low <= span:
        return low, high
    half = span // 2
    lo = _clamp(current - half, low, high)
    hi = _clamp(current + half, low, high)
    if hi - lo < span:                      # hit a wall: move the window, keep its width
        if lo == low:
            hi = min(high, lo + span)
        else:
            lo = max(low, hi - span)
    return lo, hi


def values(lo, hi, step=1):
    """Every strength in [lo, hi] at `step`, ascending, both ends included when they land on the grid."""
    step = max(1, int(step))
    if hi < lo:
        lo, hi = hi, lo
    out, value = [], int(lo)
    while value <= hi:
        out.append(value)
        value += step
    return out


def count(lo, hi, step=1):
    return len(values(lo, hi, step))


def narrow_range(lo, hi, steps=10):
    """The fallback the plan names: when the legal range is narrow, 10 steps across the whole of it."""
    if hi <= lo:
        return [lo]
    span = hi - lo
    step = max(1, int(round(span / max(1, steps - 1))))
    return values(lo, hi, step)


def plan(strengths, bases):
    """[(base path, strength)] in the order they will be rendered: each base through the whole ladder.

    Bases outer, strengths inner: the gallery then shows every strength of one picture together, which is what makes a
    sweep comparable - the earlier inline version interleaved the other way round and the folder read as noise.
    """
    return [(str(base), int(strength)) for base in bases for strength in strengths]


def name_suffix(strength):
    return '-s%d' % int(strength)


def sheet_grid(n):
    """(cols, rows) for a contact sheet: the smallest square that holds `n`, so 9 -> 3x3 and 21 -> 5x5."""
    if n <= 0:
        return 1, 1
    cols = int(math.ceil(math.sqrt(n)))
    return cols, int(math.ceil(n / cols))


def sheet_cells(n):
    """How many cells are drawn empty, which is information: a 21-value matrix is not a square and should look it."""
    cols, rows = sheet_grid(n)
    return cols * rows - n


def sheet_size(n, tile_px):
    cols, rows = sheet_grid(n)
    return cols * tile_px, rows * tile_px


def human_duration(seconds):
    """"5 min 0 s" - the form the dialog's cost line uses, because "300 s" needs converting in the head."""
    if seconds is None:
        return 'unknown'
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return '%.1f s' % seconds
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return '%d min %d s' % (minutes, rest)
    hours, minutes = divmod(minutes, 60)
    return '%d h %d min' % (hours, minutes)
