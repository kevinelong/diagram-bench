"""test_matrix.py - the arithmetic behind the matrix dialog and the hunt warning.

Run: python test_matrix.py

The cases here are the ones the plan singles out: the window must shift rather than truncate at the ends of the range,
the count must be visible before anything renders, the sheet must pad rather than stretch, and the warning must stay quiet
when the run is cheap - a warning that fires often is a warning nobody reads.
"""
from __future__ import annotations

import matrix
import estimate

FAILURES = [0]


def check(name, ok, detail=''):
    if not ok:
        print('FAIL: %s %s' % (name, detail))
        FAILURES[0] += 1


# ---------------------------------------------------------------- the default window
lo, hi = matrix.window(50)
check('a mid-range strength opens at +-10', (lo, hi) == (40, 60), (lo, hi))
check('which is 21 renders at step 1', matrix.count(lo, hi, 1) == 21, matrix.count(lo, hi, 1))
check('the coarse preset halves it', matrix.count(lo, hi, matrix.COARSE_STEP) == 11,
      matrix.count(lo, hi, matrix.COARSE_STEP))

# shift, do not truncate: the plan's named case
lo95, hi95 = matrix.window(95)
check('95 % shifts to 80-100 rather than truncating to 85-100', (lo95, hi95) == (80, 100), (lo95, hi95))
check('and still offers 21 values', matrix.count(lo95, hi95) == 21, matrix.count(lo95, hi95))
lo5, hi5 = matrix.window(5)
check('5 % shifts to 0-20', (lo5, hi5) == (0, 20), (lo5, hi5))
check('the window is always 21 wide inside the range', all(
    matrix.count(*matrix.window(c)) == 21 for c in (0, 1, 10, 50, 90, 99, 100)))

# a legal range narrower than the window uses the whole range
narrow = matrix.window(50, low=40, high=60)
check('a narrow legal range is used whole', narrow == (40, 60), narrow)
check('and the fallback gives 10 steps across it', matrix.narrow_range(40, 60, 10) ==
      list(range(40, 61, 2)), matrix.narrow_range(40, 60, 10))

# ---------------------------------------------------------------- values
check('values ascend and include both ends on the grid', matrix.values(40, 44, 2) == [40, 42, 44],
      matrix.values(40, 44, 2))
check('an off-grid end is not invented', matrix.values(40, 45, 2) == [40, 42, 44],
      matrix.values(40, 45, 2))
check('a reversed pair is tolerated, not a crash', matrix.values(60, 40, 10) == [40, 50, 60],
      matrix.values(60, 40, 10))
check('step 0 does not hang', matrix.values(40, 42, 0) == [40, 41, 42], matrix.values(40, 42, 0))

# ---------------------------------------------------------------- the plan order
bases = ['a.png', 'b.png']
strengths = [40, 50]
order = matrix.plan(strengths, bases)
check('bases outer, strengths inner', order == [('a.png', 40), ('a.png', 50), ('b.png', 40), ('b.png', 50)], order)
check('two bases at two strengths is four renders', len(order) == 4)
check('no bases means no work, not a crash', matrix.plan(strengths, []) == [])

# ---------------------------------------------------------------- naming
check('the suffix carries the strength', matrix.name_suffix(40) == '-s40' and matrix.name_suffix(7) == '-s7')

# ---------------------------------------------------------------- contact sheet packing
check('9 renders is a 3x3 sheet', matrix.sheet_grid(9) == (3, 3), matrix.sheet_grid(9))
check('21 renders is 5x5 with four empty cells', matrix.sheet_grid(21) == (5, 5) and matrix.sheet_cells(21) == 4)
check('a single render is 1x1', matrix.sheet_grid(1) == (1, 1))
check('an empty plan does not produce a zero-size sheet', matrix.sheet_grid(0) == (1, 1))
check('the sheet is square in pixels', matrix.sheet_size(9, 512) == (1536, 1536), matrix.sheet_size(9, 512))

# ---------------------------------------------------------------- durations
check('seconds are seconds', matrix.human_duration(42.5) == '42.5 s')
check('minutes carry seconds', matrix.human_duration(300) == '5 min 0 s', matrix.human_duration(300))
check('an hour is not 60 minutes', matrix.human_duration(3700).startswith('1 h 1 min'),
      matrix.human_duration(3700))
check('unknown stays unknown', matrix.human_duration(None) == 'unknown')

# ---------------------------------------------------------------- estimates come from measurements
per = estimate.wall_seconds('desktop-5070', 'z-image turbo', 1024, 9)
check('the desktop estimate is the measured row, not the 20 s placeholder', per == 14.3, per)
lap = estimate.wall_seconds('laptop-4060', 'z-image turbo', 1024, 9)
check('the laptop is 10x the desktop for the same job', lap == 142.5, lap)
check('an unmeasured model returns None, never a guess',
      estimate.wall_seconds('desktop-5070', 'flux3 klein 4b', 1024, 16) is None,
      estimate.wall_seconds('desktop-5070', 'flux3 klein 4b', 1024, 16))
check('an unknown card returns None', estimate.wall_seconds('nope', 'z-image turbo', 1024, 9) is None)
check('steps scale the estimate', round(estimate.wall_seconds('desktop-5070', 'z-image turbo', 1024, 18), 1) == 28.6,
      estimate.wall_seconds('desktop-5070', 'z-image turbo', 1024, 18))
check('the total of 21 renders is 21 x per-picture',
      round(estimate.total(21, 14.3), 1) == 300.3, estimate.total(21, 14.3))
check('an unknown per-picture cost makes the total unknown', estimate.total(21, None) is None)

# ---------------------------------------------------------------- the hunt warning
# 21 renders of turbo at 1024 on the desktop: already the fastest, so no warning
quiet = estimate.hunt_advice('desktop-5070', 'z-image turbo', 1024, 9, 21, largest_size=1024)
check('the fastest model at full size does not warn', quiet['warn'] is False, quiet)
# the same count off the largest size: cheap, so no warning either
small = estimate.hunt_advice('desktop-5070', 'z-image turbo', 512, 9, 21, largest_size=1024)
check('a smaller size does not warn', small['warn'] is False, small)
# the laptop's numbers are the case the warning exists for
slow = estimate.hunt_advice('laptop-4060', 'z-image turbo', 1024, 9, 21, largest_size=1024)
check('the laptop at full size is the slow model', slow['per_picture'] == 142.5, slow)
check('and it is also the fastest there, so it does not warn against itself', slow['warn'] is False, slow)

# an unmeasured model on a slow card: the dialog says unknown, and does not invent a warning
unknown = estimate.hunt_advice('laptop-4060', 'flux3 klein 4b', 1024, 16, 21, largest_size=1024)
check('an unmeasured model reports unknown cost', unknown['per_picture'] is None, unknown)
check('and does not warn on a guess', unknown['warn'] is False, unknown)

print('FAILURES: %d' % FAILURES[0] if FAILURES[0] else 'ALL PASS')
raise SystemExit(1 if FAILURES[0] else 0)
