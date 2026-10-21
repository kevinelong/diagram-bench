# Method B measured: a local coder writing SVG, first real numbers on this track

**Arm:** B (LLM writes SVG directly) - `qwen3-coder:30b` via Ollama, temperature 0, seed 1234+sample, one
complete SVG per request, nothing else. **Corpus:** `prompts-diagrams.json`, 10 prompts x 2 samples = **20
attempts, 19 valid documents, 1 failure**. Scoring: `trace_score.py` (vtracer at fixed parameters) on a Chromium
render at 1024x1024, plus RapidOCR against each prompt's required labels. Artifacts:
`result-method-b-qwen3-coder_30b.json`, `result-method-b-scores.json`, `svg/qwen3-coder_30b/`,
`render/`.

## The table

| metric | value |
|---|---|
| documents valid (parse) | **19 / 20** |
| axis alignment, median | **100.0 %** (16 of 19 images) |
| major colours, median | **2** (no gradients, no shading) |
| traced paths, median | **69** (range 25-323) |
| traced nodes, median | **702** |
| OCR exact, mean | **0.876** |
| images with every required label read | **14 / 19** |
| generation wall time | **8-22 s** per image, one 180 s timeout producing no `<svg>` |

## Where it failed, which is the interesting part

- **`blueprint-1` s1**: drew the bracket and dimension lines correctly (**axis 100 %**, 2 colours) and **read 0/2
  labels** - the labels A and B were never placed. Geometry perfect, teaching value zero.
- **`tree-1` both samples**: axis alignment **0 %**, which is *correct* - a binary tree needs diagonal edges - but
  OCR read only **4 of 7** node numbers. So the axis metric has to be read against the prompt's intent, not
  absolutely; a bare number would have libelled a correct drawing.
- **`network-1` both samples**: 3/4 labels, axis 71-73 % (diagonal edges to the outer boxes, again legitimate).
- **`process-1` both samples**: **309-323 paths** against a median of 69 - the swimlane was drawn with a very
  inefficient structure (many small decorative paths). It reads correctly (OCR 2/2) and traces badly. This is
  exactly the "technically faithful, practically useless" case the protocol warns about, caught by the metric.

**Prediction scored:** I predicted method B would fail on **layout, not geometry**. Confirmed, with one
refinement: the failures are **omitted labels and inefficient structure**, not overlapping text - and geometry
held at 100 % axis alignment except where diagonals were semantically required.

## Why this is the baseline that matters

Every diffusion arm now has something to beat, and the bar is split in a useful way:

- **on editability and axis alignment method B is already at the ceiling** (100 % axis, 2 colours, real `<text>`,
  1-3 KB source files). A diffusion model cannot beat that; it can only fail to reach it.
- **on label completeness method B is at 0.876**, which is a genuinely beatable number **and** a fixable one: a
  validator that checks every required label exists as a `<text>` element, then asks the model to repair, would
  take this arm toward 1.0 without any new model. That is the cheapest improvement available anywhere in this
  track, and it is a property of the *harness*, not of the model.
- **on path economy** the median of 69 is good, but `process-1`'s 309 shows the variance that a diffusion-plus-
  trace arm will have in spades.

## Caveats, stated

- **n = 2 per prompt**, one model, one temperature. This is a shape, not a rate; it says nothing about how often
  a different coder model would omit a label.
- **The render is Chromium's**, so text metrics are Chromium's too - a downstream consumer using Inkscape or
  resvg may place `<text>` differently. The source SVG is the artifact; the render is our measurement of it.
- **Generation ran concurrently with a 6.8 GB download and two application installs**, so the wall times above
  are upper bounds and are **not** used as a latency result. Only validity and structure are read from this run.
- The `ink_pct` and `straight_px_pct` columns are recorded per row in the JSON but not summarised here, because
  they have no ground-truth meaning across such different diagram kinds.
