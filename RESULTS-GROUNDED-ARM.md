# The grounded prompts, measured: coordinates for geometry, words for symbols
## 2026-10-21 — FLUX.1-schnell Q4_K_S, 1024, seed 1234, 4 steps, identical style suffix

Same model, same seed, same suffix, same scorer. The only difference is the prompt text: the frozen corpus's
one-line prompts against `prompts-diagrams-grounded.json`'s coordinate lists.

| prompt | frozen labels | grounded labels | paths f/g | axis f/g |
|---|---|---|---|---|
| flow-1 | 3/4 | 3/4 | 162/168 | 100/100 |
| floor-1 | 2/4 | **1/4** | 238/239 | 98.9/98.6 |
| bar-1 | 1/4 | 1/4 | 49/139 | 100/100 |
| org-1 | 2/3 | **1/3** | 62/84 | 100/100 |
| circuit-1 | **2/2** | **0/2** | 67/145 | 100/100 |
| blueprint-1 | 0/2 | 0/2 | 189/153 | 100/100 |
| tree-1 | 6/7 | 6/7 | 250/335 | **66.7/100** |
| timeline-1 | **3/4** | **0/4** | 71/78 | 100/100 |
| network-1 | 1/4 | 1/4 | 119/73 | 97.6/100 |
| process-1 | **1/2** | **0/2** | 77/194 | 96.2/100 |

| summary | frozen | grounded |
|---|---|---|
| `ocr_exact_mean` | **0.552** | **0.269** |
| `ocr_full_match` | **1** | **0** |
| `paths_median` | **98** | 149 |
| `axis_median` | 100.0 | 100.0 |
| `segments_median` | 37.5 | 37.5 |

## The result is negative, and the reason is visible

Grounding **halved text legibility**. It also improved axis alignment where it was bad (tree 66.7 -> 100,
network 97.6 -> 100, process 96.2 -> 100) and cost path economy (bar 49 -> 139, process 77 -> 194). So the
coordinate list did change the drawing: it made the *geometry* cleaner and the *typography* worse.

Two cases show the mechanism:

- **circuit-1**: the grounded prompt says "Rectangle 140 by 60 at 320,390" for the resistor body. The model drew a
  **single box with the literal text "RC" inside it**. The frozen prompt's words "resistor symbol ... capacitor
  symbol" carry glyph conventions that no coordinate list can express, and describing the resistor as a rectangle
  invited a rectangle for the whole schematic. 2/2 labels -> 0/2.
- **timeline-1**: the grounded prompt states four 30 px labels at x=200,400,600,800. The model produced **five**
  labels, smaller and garbled (`1920`, `2000`, `400.50`, `680.14`, `9223`). Numerals that are *content* (years) do
  not survive a prompt that spends its attention budget on coordinates.

The pool table grounded successfully because every element in it was pure geometry — rectangles, circles, straight
lines — with the only text being labels. That is the boundary, and it is now measured rather than assumed.

## The rule this establishes

**Coordinates for geometry; words for symbols and content.** A grounded prompt should anchor the skeleton
("a resistor drawn as a zigzag from 320,420 to 460,420, labelled R1 above at 390,372") rather than replace the
semantic word with the primitive it is made of ("Rectangle 140 by 60"). The same applies to text that carries
meaning: state *which* strings and *where*, not just where.

This is also why the pool-table ladder's v6 worked: the cue was geometry (a line at 30 degrees) and the labels were
short fixed strings (CUE), so both survived.

## What this does not establish

- Nothing about any other model. This is schnell Q4_K_S at 4 steps, one seed. Z-Image, klein and dev remain
  unmeasured against the grounded set.
- One seed only. The frozen set's own `ocr_exact_mean` at seed 1234 is 0.552; the seeds 1234/5678 spread is not
  measured here, so the size of the gap (0.55 -> 0.27) is not yet separated from seed noise.
- The grounded revision is still worth keeping: it is the only version of these tasks whose reference drawing
  scores 10/10 structurally and 43/43 on labels, which makes it a usable *target*. It is not, on this evidence, a
  better *prompt*.

## Next action

**Grounded v2**: keep the coordinates, restore the words — one line per element of the form
"<what it is> drawn from <coords> to <coords>, labelled <string> at <x,y>". Re-run the same arm and the same
scorer. Prediction on record: circuit-1 and timeline-1 recover to frozen levels (2/2 and 3/4) while tree-1 keeps
its 100% axis alignment, i.e. `ocr_exact_mean` above 0.552 with `axis_median` still 100.
