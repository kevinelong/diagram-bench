# The coordinate budget, measured: peak at 10 numbers, collapse by 34
## 2026-10-21 — FLUX.1-schnell Q4_K_S, 1024, seed 1234, 4 steps

Two experiments from the v2 write-up, run on one unchanged target each.

## Experiment 1 — density ladder on network-1

Same drawing every time (SERVER linked to PC1/PC2/PC3, four labels); only the amount of that drawing stated as
numbers changes. Numeric tokens counted with a regex over the prompt text.

All label counts below are **exact-token** (the matcher was corrected the same day - see `RESULTS-GROUNDED-ARM.md`).
network-1's labels are whole alphanumeric tokens, so these rungs are unaffected by the correction.

| variant | numbers | labels read | paths | axis | what the reader saw |
|---|---|---|---|---|---|
| `net-1-words` | 6 | **2/4** | 166 | 87.8 | SERVER, PC, PC, PC3 |
| `net-2-server` | 10 | **3/4** | 122 | 86.5 | SERVER, PC1, PC1, PC3 |
| `net-3-pcs` | 18 | 2/4 | 187 | 86.7 | SERVER, PC1, PC1, **PC..33, 39910, 11210, 772, 700** |
| `net-4-everything` | 34 | **1/4** | 124 | 84.4 | SERVER, **P811, P822, 71/23** |

Prediction was "flat to ~8 numbers, sharp fall after 16". **Half right**: there is no plateau — legibility peaks at
**10** numbers and then falls (3 -> 2 -> 1). The sharp part is confirmed, and the leakage is now visible at **18**
numbers: the reader sees `39910`, `11210`, `772`, `700` printed on the drawing. By 34 the labels themselves are
corrupted (`P811`, `P822`), not merely crowded.

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
