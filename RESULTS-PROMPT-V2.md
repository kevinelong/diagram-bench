# The v1 pool-table prompt was the defect: 2/7 -> 6/7 once the real geometry was stated

Kevin's diagnosis, tested. The v1 task said *"a tight triangle rack near the right end (rows of 1, 2, 3, 4 and 5)"*
and *"one at the middle of each long side"*. It never named the **foot spot**, never said which way the **apex**
points, never required the **back row parallel to the foot rail**, and never placed the cue ball. Two of the models
then produced tables that were wrong in exactly those unstated ways, and my v1 checks - which **counted** circles
instead of **locating** them - passed them.

## Measured, before and after

| drawing | prompt | score (v2 checks) | what failed |
|---|---|---|---|
| bare | **v1** (unstated geometry) | **2/7** | side pockets on the **short** rails, apex off centre, back row not parallel, cue ball misplaced, cue 0 degrees |
| kit (line_at) | **v1** | 3/7 | apex, back row, cue ball |
| **bare** | **v2** (geometry stated) | **6/7** | cue ball spot only |
| **kit (line_at)** | **v2** | **5/7** | apex, back row |

**The prompt was the difference, not the model.** With the table's real geometry written down - long axis
horizontal, foot end at the right short rail, apex on the foot spot pointing at the head end, back row parallel to
and just inside the foot rail, side pockets at the exact midpoint of each **long** rail, cue ball on the centre line
at the head quarter, cue at about 30 degrees - the bare configuration went from **2/7 to 6/7**, including the two
checks it had failed completely: **apex on the centre line** and **back row parallel**.

## Three detector bugs found while doing this

The v2 checker was wrong three times before it could judge anything, all of them mine:

1. **The pockets rule demanded `short_rail == 2`** while counting corners separately, so a *correct* table failed
   it. Corrected to "2 long-rail side pockets, 4 corners, **0** short-rail".
2. **Cue angle was read without normalising direction**: a cue drawn tip-to-butt reads **-153.4 degrees**, whose
   *axis* is 26.6 degrees. The check now folds the angle into 0-90 before testing.
3. **The table rect was being discarded as a "background"** whenever the SVG's own width and height equalled the
   table's - which a well-formed drawing does. The rule now keeps the largest rect when it is the only candidate.

## What is left, and what it says

The two remaining failures are real and specific: the **cue ball is not at the head quarter** in the v2 bare
drawing, and the **rack apex / back row** still miss in the kit drawing. Neither is ambiguity any more - the prompt
states both - which makes them genuine model errors of the same family as `VAILDATE`: the instruction is present,
and the model does the approximate version.

That is the useful split this exercise keeps producing: **prompt ambiguity produces wrong drawings that look
right, and only geometric checks tell you which of the two you have.**
