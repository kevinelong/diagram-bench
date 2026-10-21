# Giving B what E had: the primitive vocabulary is the lever, and my checker nearly hid it

The synthesis predicted: *give method B an angle-forcing primitive and a checker-driven repair loop, and its
horizontal-cue failure disappears.* Half of that was right, the other half was wrong for an instructive reason.

## What was tried, in order

| configuration | cue angle produced | verdict |
|---|---|---|
| B, bare spec (earlier run) | 0° (horizontal rect) | fail |
| B + worked SVG example | 0° (still horizontal) | fail |
| B + **kit with `line_at(x,y,degrees,length)`**, `<rect>` still allowed | 0° in **3/3** attempts - the model drew the cue as a `<rect>` 128x8 and **used no lines at all** | fail |
| B + kit + **`<rect>` banned**, table drawn as four lines | **-11.3°, 30.0°, 30.0°** - off-axis in **3/3** | **pass** |

## Why the kit alone did nothing, and the ban worked

**SVG's natural primitive for a long thin object is an axis-aligned `<rect>`.** The model reached for it every
time, and a rect cannot express an angle without a `transform` - so the angle-capable helper sat unused while the
model produced a perfectly valid, perfectly horizontal cue. The vocabulary *available* did not change the drawing;
the vocabulary **withdrawn** did.

That is a sharper version of the E lesson: E's `seg()` takes two endpoints, which makes every angle trivially
expressible, and E used it. B's `<rect>` makes every angle *inexpressible*, and B used that. **The medium's
primitives bound what the model will even consider drawing.**

## And the checker nearly buried the result

The kit run was first scored as a failure: my acceptance test demanded a cue within **20-45°** and a
**stroke-width >= 4**. Both were invented - the prompt says only *"angled toward the cue ball"* and specifies no
degrees, and the model omitted `stroke-width` (defaulting to 1). Under the prompt's actual requirement, **all three
lines-only attempts pass**, two of them at exactly **30.0°**, which is the number the kit's trigonometry invites.

**That is the eighth or ninth instrument error of this exercise, and it is the same shape as the others**: a check
that encodes my assumption rather than the specification's requirement. Had I not opened the SVG and looked at the
five lines, the conclusion would have been "the prediction failed".

## What this settles about giving both methods E's advantages

1. **A worked example is necessary but not sufficient.** Alone it took E from 0/7 to 6/7, and it did nothing for
   B - because an example shows *surface*, and B needed its *vocabulary* restricted.
2. **The primitive set is the real lever.** Ban the primitive that cannot express the requirement, and the model
   computes. Keep it, and the requirement silently degrades to the easy shape.
3. **Checker-driven repair is untested as a repair mechanism** - in these runs it never fired usefully, because
   the model's first attempt already used the wrong primitive. Fixing the vocabulary beat retrying the message.
4. **Acceptance criteria must be quoted from the task, not invented.** My 20-45° window and stroke-width floor were
   fabrications that would have failed a correct drawing; the fix is one line in the checker and a hard look at the
   artifact.

## Remaining defect in this configuration

`pooltable-kit-lines-try3.svg` is **not well-formed XML** (invalid token at line 16, column 31) even though its cue
line is correct at 30°. So validity, not geometry, is now B's open failure in the lines-only medium - the same axis
on which method E fails when its script does not run.
