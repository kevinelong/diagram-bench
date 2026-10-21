# Instrument validation, 2026-10-20: the tracer scores are checked against ground truth

Before any model weight is downloaded, the scorer has to prove two things: that it recovers a known diagram's
properties, and that it **discriminates** against the thing we are trying to penalise. Both were run with
`trace_score.py` on images drawn here, whose properties we know exactly.

## Positive control: a hand-drawn flowchart

Ground truth: **4 rectangles, 3 vertical connectors, 4 labels, 2 ink colours, 100 % axis-aligned.**

| metric | measured |
|---|---|
| paths | **37** (4 boxes + 3 connectors + ~30 glyph contours, and no stray speckle) |
| nodes | 447 |
| nodes per path | 12.1 |
| SVG size | 17.1 KB |
| axis alignment | **100.0 %** ✓ |
| straight-edge coverage | 60.0 % (glyph outlines are the rest, which is correct) |
| major colours | **2** ✓ |
| ink coverage | 3.0 % |

## Negative control: a soft radial gradient, nothing else

This is the shape the brief warns about - shading that explodes into overlapping paths.

| metric | diagram | gradient | ratio |
|---|---|---|---|
| paths | 37 | **164** | 4.4x |
| nodes | 447 | **3,044** | 6.8x |
| SVG size | 17.1 KB | **170 KB** | 10x |
| major colours | 2 | **30** | 15x |
| straight-edge coverage | 60.0 % | **11.9 %** | 0.2x |
| segments detected | 50 | **4** | - |

**The scorer separates them by up to 15x on every axis that matters**, which is what an instrument has to do
before it is allowed to rank models.

## One metric that does not work, found by the control

**`flat_pct` does not detect gradients.** It measures the share of pixels *without a sharp edge*, and a smooth
gradient has almost no sharp edges: the gradient image scored **93.6 %** flat against the diagram's 97.7 %. The
metric's name promised something it does not do. **`colours_major` is the gradient detector** (2 vs 30), and so
is SVG size. `flat_pct` stays in the output as a measure of edge-harshness only, with the caveat written down
rather than quietly corrected.

## What this establishes, and what it does not

- Establishes: the trace path, the line detection, the colour counting and the axis measure all work, are
  deterministic, and rank a clean diagram above a traced gradient.
- Does **not** establish: that a diffusion model's output scores the same way a synthetic drawing does. Model
  output has anti-aliasing, JPEG-like ringing and imperfect geometry; the first real arm is what tests that.
- Still missing before any arm is scoreable: **OCR** (rapidocr, ~90 MB pip) and **SVG re-rasterisation**.
  `cairosvg` was the obvious choice and **fails on this box** - it needs a native `libcairo-2.dll` that is not
  installed - so re-rastering will go through **Chromium** (already available through the browser tooling) with a
  fixed viewport and device scale factor of 1.
