# Loading UX: the rule, and the meter that implements it

Asked for 2026-10-31: *"a large green percent loading bar [XXX...] that is graphical but also shows numeric
percentage e.g. 50% and any other information that is known about the loading like 'Now categorizing 1427 images into
logical group folders', and any other stages before or after ... and remember to do the same for any other loading
experience in this app and any others we make when loading is longer than 1s."*

## The rule

Any operation that can exceed **one second** reports through `LoadingMeter`. No exceptions, no spinner-only states.

| Duration | What the user sees |
|---|---|
| < 1 s | Nothing. A meter that appears and vanishes is worse than a 300 ms pause. |
| 1–3 s | The meter, with the percentage **if the work can be measured** and the stage named. |
| > 3 s | The meter, the percentage, the stage, and the detail line ("600 of 1427 pictures"). An elapsed/remaining line appears once progress passes ~2 %. |
| Failing | The meter hides. Never leave a bar at 40 % after the work died. |

Two rules that matter more than the pixels:

1. **The percentage is measured, never animated.** If a stage cannot report progress, `set(stage=...)` alone moves the
   words and leaves the number where it was. A bar that creeps forward on a timer is a lie, and a lie is worse than
   "working".
2. **Name the stage in the user's words.** "grouping pictures into folders", not "plan()". The point of the meter is
   that someone can tell what is happening and how much is left.

## The widget

`LoadingMeter` in `zimage_ui.py` — a Canvas, not a `ttk.Progressbar`, because the Windows `vista` theme ignores
`background`/`troughcolor` and a themed bar cannot be made green. It draws `[ ###      ]` with the fill in `#22c55e`,
a 40 px percentage above it, and the stage / detail / elapsed lines below.

```python
meter.show('grouping pictures into folders', 'reading the gallery')   # 0 %, named stage
meter.set(0.42, 'grouping pictures into folders', '600 of 1427 pictures')
meter.set(stage='labelling the folders')                             # words only; the number holds
meter.hide()
```

`show()` starts the elapsed clock. `set()` with no fraction never invents progress. `hide()` also clears the clock, so a
second use starts from zero.

## Wiring a new long pass (the recipe)

The meter lives on the UI thread; the work usually does not. So the worker posts to the existing `ui_events` queue and
the poll applies it — the same path every other worker message uses:

```python
# in the worker
self.ui_events.put(('load', (done / float(total), 'rendering the clip', '%d of %d frames' % (done, total))))

# in the poll's ui_events loop (already there)
elif kind == 'load':
    frac, stage, detail = payload
    self.load_meter.set(frac, stage, detail)
```

and around the work: `self.load_meter.show(...)` before, `self.load_meter.hide()` when it lands *or* fails.

### What reports today

| Pass | Stage text | Percentage source |
|---|---|---|
| Grouping the gallery | reading the gallery → fitting the vocabulary → linking prompt series → grouping prompts into folders → labelling | real: sidecars read, then the roots pairwise loop, then the clustering loop (`group_texts(..., progress=)`), 1.0 at the end |
| Describe-all | describing the pictures | real: pictures written / total |
| Generation | the existing progress bar and per-tile ticks in the prompt column | already counted |

### What still should

- **Thumbnail prefetch on a cold folder** — `thumbs_for` builds in a pool with no per-item callback; it needs a count
  passed in and a post per completion. This is the next one, and it is the one a fresh 1400-picture folder pays.
- **The initial scan** on a slow disk, if it ever measures over a second.
- **Clip rendering**, which currently reports per-tile in the prompt column rather than through the meter.

## For other apps

This is deliberately not z-image-specific: it is a `ttk.Frame` with a Canvas and three labels, and the contract is the
table above. Copy the class, keep `show/set/hide` and the measured-only rule, and the loading experience is consistent
across the family of tools. A spinner is never the fallback — a stage name is.

## Measured

- Grouping 1427 pictures: **5.1 s** (p90 prompt 911 words). That is why the meter exists.
- Real gallery at the time of writing: 1414 grouped pictures, 53 folders, "53 folders from 1414 pictures in 4.5 s".
