# The grounded prompts, measured twice: coordinates leak, and the pool table was the easy case

## CORRECTION (same day): the label matcher was counting substrings

Everything below was first scored with `any(label in found_text)`, which credits **`7` inside `70`** and **`120`
inside `1120`**. Found by looking at the tree render: the model had labelled its nodes `70, 40, 66, 30, 212, 15` —
the prompt's own radius and coordinates — and the matcher still reported 5/7. `score_arm.py` now matches **exact
tokens**, and every arm was re-scored. Ordering survives; the absolute numbers and two specific claims do not.

| summary | frozen | v1 coords | v2 words+coords |
|---|---|---|---|
| `ocr_exact_mean` (substring) | 0.552 | 0.269 | 0.437 |
| **`ocr_exact_mean` (exact token)** | **0.406** | **0.183** | **0.337** |
| `paths_median` | 98 | 149 | 104 |

Retracted, because they were artefacts of substring matching:

- **blueprint-1 v2 "0/2 -> 2/2"** is **0/2 -> 0/2**. The reader had seen `1120`, and `120` is a substring of it.
- **tree-1 frozen "6/7"** is **1/7**. Every digit was matching inside a coordinate or a radius.

What survives:

- The ordering: frozen > v2 > v1, on both matchers.
- **network-1 v2 3/4** (SERVER, PC1, PC3 as whole tokens) - a real gain from the link sentence.
- **timeline-1 v2 2/4** - a real recovery from 0/4.
- **circuit-1 stays 0/2** in both grounded revisions, and the coordinates still appear as text on the drawing.

Corrected per-prompt table:

| prompt | frozen | v1 | v2 |
|---|---|---|---|
| flow-1 | 3/4 | 3/4 | 2/4 |
| network-1 | 1/4 | 1/4 | **3/4** |
| timeline-1 | 2/4 | 0/4 | 2/4 |
| floor-1 | 2/4 | 1/4 | 1/4 |
| org-1 | 2/3 | 1/3 | 1/3 |
| process-1 | 1/2 | 0/2 | 1/2 |
| bar-1 | 1/4 | 1/4 | 1/4 |
| circuit-1 | 1/2 | 0/2 | 0/2 |
| blueprint-1 | 0/2 | 0/2 | 0/2 |
| tree-1 | 1/7 | 0/7 | 2/7 |

**Historical numbers elsewhere in this repo are inflated the same way** wherever a required label is a short token or
a digit: `result-arm-flux.json`, `result-schnell-512.json` and the rest were scored with the substring matcher. They
are not comparable to anything re-scored after today without a re-run of that matcher.

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

## v2: semantic phrases restored — prediction failed

`prompts-diagrams-grounded.json` was regenerated so every element leads with what it *is* ("the resistor body, a
symbol and not a plain box", "the year label 2020", "an edge of the tree") before its coordinates. Same arm, same
seed, same scorer. v1's prompts are preserved as `prompts-diagrams-grounded-v1.json`.

| summary | frozen | v1 (coords only) | v2 (words + coords) |
|---|---|---|---|
| `ocr_exact_mean` | **0.552** | 0.269 | 0.437 |
| `ocr_full_match` | 1/10 | 0/10 | 1/10 |
| `paths_median` | **98** | 149 | 104 |
| `axis_median` | 100.0 | 100.0 | 100.0 |

| prompt | frozen | v1 | v2 |
|---|---|---|---|
| blueprint-1 | 0/2 | 0/2 | **2/2** |
| network-1 | 1/4 | 1/4 | **3/4** |
| timeline-1 | 3/4 | 0/4 | 2/4 |
| process-1 | 1/2 | 0/2 | 1/2 |
| circuit-1 | 2/2 | 0/2 | **0/2** |
| tree-1 | 6/7 | 6/7 | **2/7** |
| flow-1 | 3/4 | 3/4 | 2/4 |
| floor-1 | 2/4 | 1/4 | 1/4 |
| bar-1 | 1/4 | 1/4 | 1/4 |
| org-1 | 2/3 | 1/3 | 1/3 |

**The v2 prediction was wrong on all three counts.** I predicted circuit-1 back to 2/2, timeline-1 to 3/4 and
`ocr_exact_mean` above 0.552. Actual: 0/2, 2/4, 0.437.

## What the v2 images actually show

- **circuit-1**: still one box labelled `RC` — and now the reader sees **`241`, `864`, `201`, `-500`, `C8`** on the
  drawing. The coordinates themselves were rendered as text. A 15-element prompt carries ~30 numbers, and at 4
  steps the model prints some of them.
- **tree-1**: seven nodes drawn, but the numbers **repeat as 1,1,1,1,5,5**. The phrase "the number inside a node"
  is generic, so identity is lost: the model keeps the first digit it saw rather than binding 3 to the third node.
- **blueprint-1**: the one clear win (0/2 -> 2/2) — `BRACKET` and the dimension text are large, isolated strings.
- **network-1**: 1/4 -> 3/4 — the link sentence ("a link from the server to PC1") is what made the connectors and
  their targets cohere.

## The refined conclusion: coordinate density, not coordinates

The pool table's v6 succeeded with **two** coordinate pairs and a single short label. Circuit and timeline fail with
**30**. The mechanism is now consistent across both revisions:

1. Numbers compete with the semantic content for attention, so text legibility drops.
2. Above some density the model **renders the numbers it was given** (circuit's `241`, `864`, `201`).
3. Generic repeated phrases lose **identity binding** (tree's repeated `1`s) — each element needs its own identity in
   the sentence ("node 3: a circle at 212,620 containing 3"), not a phrase shared seven times.

So grounding is not a spectrum from "vague" to "exact" for this model; it has a budget. The frozen one-liners win on
this corpus not because they are precise but because they are *short*, and they let the model use its own glyph
conventions.

## Next action

Two cheap, separable experiments, both with predictions on record:

1. **Density ladder on one task** (bar-1, already 1/4 in all three): generate the same drawing at 4, 8, 16 and 30
   stated numbers, everything else fixed. Prediction: `ocr_exact_mean` is flat to ~8 and falls sharply after 16.
   This sizes the budget instead of guessing at it.
2. **Identity binding on tree-1**: change only the number sentences to "node N is the circle at X,Y containing N".
   Prediction: tree returns to 6/7 or better without any other change.

Do not attempt a third grounding revision of all ten until the density ladder says where the budget is.
