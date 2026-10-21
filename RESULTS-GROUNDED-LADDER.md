# The coordinate budget, measured: peak at 10 numbers, collapse by 34
## 2026-10-21 — FLUX.1-schnell Q4_K_S, 1024, seed 1234, 4 steps

Two experiments from the v2 write-up, run on one unchanged target each.

## Experiment 1 — density ladder on network-1 (replicated, 3 seeds)

Same drawing every time (SERVER linked to PC1/PC2/PC3, four labels); only the amount of that drawing stated as
numbers changes. **The first pass used one seed per rung and its curve was noise** — see the retraction below.
These are three seeds each (1234, 5678, 9012), exact-token matching, at both canvas sizes.

| variant | numbers | 512 px per-seed | 512 mean | 1024 px per-seed | 1024 mean |
|---|---|---|---|---|---|
| `net-1-words` | 6 | 3, 2, 3 | 2.67/4 | 2, 1, 2 | 1.67/4 |
| `net-2-server` | 10 | 3, 3, 2 | 2.67/4 | 3, 4, 3 | **3.33/4** |
| `net-3-pcs` | 18 | 1, 3, 2 | 2.00/4 | 2, 3, 3 | 2.67/4 |
| `net-4-everything` | 34 | 3, 1, 4 | 2.67/4 | 1, 1, 2 | **1.33/4** |

Overall: **512 px 0.625**, **1024 px 0.562** (12 images each).

What this supports, and what it does not:

- **At 512 px there is no density trend at all.** The four rungs sit inside one band (2.00-2.67) and the 34-number
  rung scored [3, 1, 4] — the widest seed spread of the whole experiment.
- **At 1024 px one comparison survives**: the 34-number rung (1.33, range 1-2) is worse than the 10-number rung
  (3.33, range 3-4) with non-overlapping seed ranges. That is a *top-rung penalty against the best rung*, not a
  curve. The shape is non-monotone (6 numbers scores *lowest* at 1.67), so "peak at 10" is not established either.
- n = 3 per rung. Nothing here survives a serious test at that size; it is enough to show the effect is not large
  and is not a clean function of the count.

### Retraction: the single-seed curve

The first pass ran one seed and produced 6 -> 2/4, 10 -> **3/4**, 18 -> 2/4, 34 -> **1/4** at 1024, and the same
prompts at 512 produced 3, 3, **1**, **3** — non-monotone within the same four rungs. That is what a noise-dominated
series looks like. The claims written from it — "the knee is at 10", "leakage at 18", "collapse by 34" — are
**withdrawn**. What remains true is qualitative and was seen repeatedly at both sizes: **at 18 and 34 numbers the
prompt's coordinates appear on the drawing as labels** (`39910`, `11210`, `772`, `700`, `122701`, `PC1703`).

## Experiment 2 — identity binding on tree-1

Only the number sentences changed: "node 3 is the circle at 212,620 containing the number 3", instead of a phrase
repeated seven times. 51 numeric tokens.

| variant | numbers | labels (exact) | axis | reader saw |
|---|---|---|---|---|
| `tree-1-ident` | 51 | **0/7** | **25.0** | 10, 22, 10, 50, 212, 50, 15 |
| `tree-1-boundary` | 15 | **1/7** | **100.0** | 70, 40, 66, 30, 1, 40, 10 |

Prediction was "back to 6/7 or better". **Wrong**, and the render shows why: the tree's *geometry* is correct in both
variants (seven nodes, three levels, six edges), but the nodes are labelled with **the prompt's own numbers** - the
radius `70`, the row `40`, `66`, the coordinate `212` - not with 1..7. Under exact matching that is 0/7 and 1/7.

The **boundary shape** (`tree-1-boundary`, 15 numbers) did what was predicted for *structure*: axis **100.0** against
25.0 for the schema shape, at a quarter of the numbers. What it did not do is bind the labels.

## What this establishes

**Density dominates binding.** The tree's identity-binding phrase was the right idea in isolation, but at 51 numbers
the model cannot hold any of it: the drawing degrades structurally. Coordinating a correct fix requires first
getting the count down to single digits per element family, which for this model means **stating a boundary, not a
schema**: start point, spacing, and count — the way the rack was stated as a lattice, not as 28 centre coordinates.

The measured budget, on this model at 4 steps:

| numeric tokens | effect on text |
|---|---|
| <= 10 | usable; labels mostly survive |
| ~18 | labels degrade and **coordinates start appearing as text** |
| >= 34 | labels corrupted, structure begins to fail |

## Infrastructure finding (cost two failed runs)

The ComfyUI **desktop app self-terminated ~30 s after start**, twice in a row, killing the second request mid-arm
(exit code 0; its auto-updater had queued desktop 1.1.7). Running ComfyUI's python server directly is stable:

```
"C:/Users/kevin/Documents/ComfyUI/.venv/Scripts/python.exe" \
  "C:/Users/kevin/AppData/Local/Programs/ComfyUI/resources/ComfyUI/main.py" \
  --user-directory C:/Users/kevin/Documents/ComfyUI/user --output-directory C:/Users/kevin/Documents/ComfyUI/output \
  --base-directory C:/Users/kevin/Documents/ComfyUI \
  --extra-model-paths-config C:/Users/kevin/AppData/Roaming/ComfyUI/extra_models_config.yaml \
  --log-stdout --listen 127.0.0.1 --port 8001
```

Arms take `COMFY_HOST=http://127.0.0.1:8001`. The desktop app serves **8000**, not the 8188 the arm scripts
originally hard-coded.

## Correction: resolution is an uncontrolled moderator

Every experiment here ran at **1024**. The re-scored 512-px arms show FLUX.1-schnell reading tree-1 at **4/7** at 512
against **1/14** at 1024 with unchanged prompts, so "the tree cannot bind digits" is false as stated - it cannot at
1024. The budget below is a 1024 measurement; whether it shifts at 512 is unknown and untested.
See `RESULTS-EXACT-RESCORE.md`.

## What this does not establish

- One model, one seed, one sampler, 4 steps. Step count is a plausible moderator: 20 steps may hold more of a
  long prompt, and this says nothing about it.
- The token counts are lexical (`-?\d+(\.\d+)?`), so "1024" and the style suffix's numbers count too. They are
  constant across rungs, so the *delta* is sound, but the absolute "budget of 10" is a property of these prompts.
- No claim that another model (Z-Image, klein, dev) shares the budget.

## Next action

A **boundary-stated** prompt for the tree: "seven circles of radius 70 on a 1000-wide canvas, four in the bottom row
at y = 620 spaced 200 apart starting at x = 212, two above at y = 360, one at the top at 512,150, each containing its
own number 1..7, edges from parent to child" — roughly a dozen numbers total instead of 51. Prediction on record:
tree-1 reaches **6/7 or better with axis above 90**, at a number count below 20.
