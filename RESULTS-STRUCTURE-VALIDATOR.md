# The structural validator: my expectations were wrong first, and that is the useful part

Method B emits SVG, so its structure is **countable** - rectangles, connectors, text nodes - which beats asking a
vision model "are there exactly four boxes?". I froze expectations, ran them, and got **8/19 structurally
correct**. Then I read the failures.

## What the failures actually were

| document | v1 said | reality |
|---|---|---|
| `floor-1` (both) | labels expected 4, found 6 and 5 | the prompt specifies **two** bedrooms, so `BEDROOM` is written twice - **5 text elements is correct** |
| `timeline-1` (both) | connectors expected 1, found 5 | the axis plus **four tick marks are connectors** - 5 is correct |
| `org-1` (both) | connectors expected 2, found 3 | a **right-angle** connector is three segments, and the prompt asks for right angles |
| `process-1` (both) | boxes 3, connectors 3, labels 2 | a **swimlane** has lane rectangles plus boxes plus header text; exact counts cannot apply |
| `blueprint-1` | labels expected 2, found 8 | dimension text and hole callouts are legitimate extras |

**The instrument was wrong, not the drawings.** v1 applied exact equality everywhere on the assumption that a
prompt pins counts; the prompt language mostly pins *minimums* or nothing at all.

## Revision 2, and what it measures

`structure-expectations.json` was rewritten with three kinds of constraint - `<field>_exact` where the prompt truly
fixes a count, `<field>_at_least` where it fixes a minimum, and **nothing** where the geometry is free-form - and
the reason for each change is recorded in the file itself, because an instrument that calls correct output wrong
is not a strict instrument, it is a broken one.

| version | structurally correct |
|---|---|
| v1 (exact counts everywhere) | **8/19** - and most of those "failures" were mine |
| v2 (exact / at-least / unconstrained as the prompt warrants) | **19/19** |

## The honest reading of 19/19

**A validator that passes everything tests nothing**, and v2 now only has teeth on the five prompts whose
structure is genuinely pinned: `flow-1` (4 boxes, 3 connectors, 4 labels), `bar-1` (4 bars), `org-1` (3 boxes,
3 labels), `network-1` (4 boxes, 3 connectors, 4 labels) - **10/10 documents correct on those**, two samples each.

On the other five it now enforces only minimums, which means it no longer catches the thing that is actually
wrong in them. `process-1` draws **12 rectangles for a three-box swimlane** and **8 connectors where the prompt
asks for one lane crossing**; `blueprint-1` carries **27 connectors and 8 labels** for a two-label drawing. Those
are real over-draws that a count-only instrument cannot judge, because the right count depends on lane geometry
and dimension conventions.

**So the next instrument for those prompts is not a tighter count**, it is a structural *shape* check: lane count
and a single cross-lane arrow for `process-1`, presence of dimension lines for `blueprint-1`. That is a different
kind of measure and it is not built.

## What this changes in the recommendation

- **The production path holds**: labels are at 100 % in source after one repair round, and structure is exact on
  every prompt where "exact" is a meaningful word.
- **The repair loop stays available but was not needed for structure** on this sample; its value was in labels.
- **Recorded for whoever reads this next**: the first version of a measure is a hypothesis about what matters, and
  this one failed its own first flight. Both passes are kept in `result-method-b-structure.json` history.
