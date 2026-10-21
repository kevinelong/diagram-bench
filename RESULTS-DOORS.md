# Door-gap check: the property the prompt actually asks for, and it discriminates

`floor-1` asks for *"thick black walls on a white background, rooms labelled ..., one door gap in each room
wall"*. Closure cannot express that - doors connect rooms by design - so `validate_doors.py` measures the
specification directly: find the **internal partitions** (runs of columns or rows where the wall mask covers a
large share of the span), then count **gaps** (non-wall runs of at least 10 px) inside each partition's span.

## Two calibration steps, both mine

1. **v1 counted the outer walls as partitions** (they span the whole canvas, so they are bands with zero gaps) and
   failed both drawings. Outside walls are not room walls, so bands within 10 % of an edge are now excluded as
   **perimeter**.
2. **v1 also demanded two vertical partitions and one horizontal one.** With perimeter excluded, the drawing that
   is right has **2 vertical + 2 horizontal**, and the one that is wrong has **2 vertical + 0 horizontal** - so the
   rule is now *at least one internal partition in each direction, each carrying one or two door gaps*, and the
   content of the check is the gap count, not a grid shape I imposed.

## Result

| document | vertical partitions (gaps) | horizontal partitions (gaps) | verdict |
|---|---|---|---|
| `floor-1-s0` | 2 (2, 2) | **0** | **fail** - no horizontal partition, so the two rows of rooms are open into each other |
| `floor-1-s1` | 2 (1, 1) | 2 (2, 2) | **pass** - every internal wall carries door gaps |

This matches the render exactly: in s0 the upper and lower rooms share one tall space, which is why a reader sees
`BEDROOM` above `KITCHEN` with nothing between them.

## Where this leaves method B's floor plan

Three separate defects, three separate instruments, all on one prompt:

| defect | instrument that sees it |
|---|---|
| `BATH` written twice | label multiset |
| no horizontal partition | door-gap band scan |
| (and the label repair earlier added the missing `A`/`B` on the blueprint) | source label check |

**The lesson repeats**: each of the two calibration steps here was mine, not the model's - the outer walls are not
room walls, and a grid shape is an assumption rather than a requirement. The check now measures the sentence in
the prompt instead of my picture of it.
