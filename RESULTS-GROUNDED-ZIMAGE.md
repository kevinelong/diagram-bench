# Z-Image against the grounded prompts: the strong model is hurt more
## 2026-10-21 — Z-Image Turbo (nvfp4), 1024, 4 steps, seeds 1234 + 5678, 20 images per arm

Same corpus pair as the schnell experiment, same scorer (exact-token), same style suffix. Z-Image is the only model
here that reads labels reliably, so it is the one that can answer whether precision *helps* a competent renderer.

| arm | labels (of 72) | `ocr_exact_mean` |
|---|---|---|
| frozen one-liners | **59/72 = 0.819** | **0.786** |
| grounded (coordinates + words) | 40/72 = 0.556 | 0.537 |

**Grounding costs the strong model a quarter of its labels.** The hypothesis it was meant to test - that precise
coordinates would let a capable model do better - is refuted.

| prompt | frozen | grounded |
|---|---|---|
| flow-1 | 8/8 | 8/8 |
| org-1 | 6/6 | 6/6 |
| circuit-1 | 3/4 | 3/4 |
| blueprint-1 | 0/4 | 0/4 |
| bar-1 | **8/8** | 5/8 |
| network-1 | 6/8 | 4/8 |
| tree-1 | **12/14** | 7/14 |
| timeline-1 | **8/8** | 3/8 |
| process-1 | **4/4** | 1/4 |
| floor-1 | 4/8 | 3/8 |

The tasks that collapse are exactly those where the short wording already carried the drawing: **timeline 8/8 -> 3/8**
(two year labels per seed become illegible), **process 4/4 -> 1/4**, **bar 8/8 -> 5/8**. What survives untouched is
what the coordinates *do* specify well - a flowchart's boxes, an org chart's boxes, a circuit's wires.

## Across three arms, the same direction

| model | frozen | grounded | delta |
|---|---|---|---|
| Z-Image Turbo | 0.786 | 0.537 | **-0.249** |
| FLUX.1-schnell Q4_K_S | 0.406 | 0.337 | -0.069 |
| (grounded v1, coordinates only, schnell) | 0.406 | 0.183 | -0.223 |

So the damage is not a weak-model artefact and not an artefact of *how* the coordinates are phrased: it is the
presence of a number schema in the prompt, and the better the model, the more it has to lose, because the frozen
one-liner was already enough for it.

## A stability check that came free

Z-Image's frozen arm scores **0.786 today** against **0.798** in the historical exact re-score - 1.5 % apart, on a
different stack (comfy-aimdo 0.2.12 -> 0.5.6, comfy-kitchen 0.2.8 -> 0.2.39). Same prompts, same seeds, same
sampler. That is the tightest reproducibility evidence this track has, and it means the historical zimage numbers are
usable as a baseline.

## What this establishes

1. **Do not ground these ten prompts by coordinate list, on any model tested.** It costs labels on every arm, and the
   more capable the renderer, the larger the absolute loss.
2. The earlier pool-table result stands and is not a contradiction: there the model had *two* coordinate pairs and a
   single short label, and the task's failure had been shown to be articulation (v5's prose clause made it worse,
   v6's arithmetic fixed it). Grounding a task *to repair a demonstrated failure* is not the same act as grounding
   ten tasks whose wording already worked.
3. `prompts-diagrams-grounded.json` keeps its value as a **reference target** (its own drawings score 10/10
   structurally and 43/43 on labels) - but not as a prompt set.

## What this does not establish

- Nothing at 512 px for Z-Image, and nothing beyond 4 steps. Step count is still an untested moderator.
- Nothing about klein or dev against the grounded set.
- The frozen corpus remains the corpus of record; no score from before today changes.
