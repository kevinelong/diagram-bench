# v5 and the work-package round: the prose constraint did not fix the inversion

## The end-to-end check after five merges

One generation (seed 20261021, 50.7 s), scored by the merged checker:

| version | prompt says about the cue | score (11 checks) | failing |
|---|---|---|---|
| v3 | "angled about 30 degrees ... butt toward the head rail" | **9/11** | `back_row_parallel`, `cue_butt_side` |
| v4 | + rack lattice formula | **10/11** | `cue_butt_side` |
| **v5** | + *"butt lies on the head side, so its x < 25"* | **9/11** | `cue_angle`, `cue_butt_side` |

**v5 is worse than v4.** The drawing puts the cue from `(23.88, 25.00)` to `(40.62, 19.50)`: length 17.6, axis
**-18.2 degrees**, butt at **x = 40.62** - still on the rack side, and now too shallow for the 20-45 degree window
that v4 passed at 30.0.

## Why, and what it confirms

The rack was fixed by **arithmetic** (a lattice formula) and the cue was not: it stayed prose. A sentence saying
"the butt's x is less than 25" did not become a coordinate any more than "rows widen toward the foot rail" became a
triangle. The model satisfied the *wording* of the new clause while breaking the *angle* clause it had previously
satisfied - exactly the pattern this exercise keeps finding: **prose constrains adjectives, and the drawing degrades
to whichever reading is easiest.**

Also note the tip moved off the ball centre (23.88 instead of 25), which no check currently asserts.

## What the merged tooling says about it

The checker caught both failures, which is the point of the round: before the P1 package, `cue_butt_side` did not
exist and v5 would have scored 10/10 while pointing the wrong way.

## The next step this implies, stated but not taken

Express the cue the way the rack was expressed - as coordinates:

```
tip  = (25 - 1.125*cos(30deg), 25 - 1.125*sin(30deg))     # one radius behind the ball
butt = (25 - 30*cos(30deg),    25 - 30*sin(30deg))        # 30 units back toward the head rail
```

That is the same lesson as the lattice, applied to the one element still described in words.
