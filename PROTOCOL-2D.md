# Image-model instrument, v2: 2D output that must auto-trace to vector

Revision of `PROTOCOL.md` for the actual goal: **charts, blueprints, floor plans and schematics whose geometry
survives a raster-to-vector tracer and whose labels are readable.** This changes the metric, the corpus and the
models - it does not change the discipline (predictions first, gates never lowered, failures counted).

## Why the objective changes the measurement

"Looks clean" is an opinion. **"Traces cleanly" is a number.** The chain is:

```
prompt -> model -> PNG -> auto-tracer -> SVG -> re-raster -> compare to PNG
                                   \-> count paths and nodes
```

An image is *good* for this job when a tracer can rebuild it: high re-raster agreement, few paths, few nodes, no
gradients, and text the OCR can read verbatim. Shadows, soft shading and 3D perspective each explode into
hundreds of overlapping paths, which is exactly what the brief warns about - so those become **penalties in the
score**, not style notes.

## Scores, all automatic

| score | definition | what a bad value means |
|---|---|---|
| **OCR exact** | fraction of the prompt's required strings found verbatim by a deterministic OCR engine | labels are unreadable or misspelled - the image is useless even if pretty |
| **trace fidelity** | SSIM between the PNG and the SVG re-rasterised from it (vtracer, fixed params) | the shape cannot be represented as clean vector primitives |
| **path economy** | SVG `path` count and total node count, **reported beside fidelity**, never instead of it | a 2,000-path trace is technically faithful and practically useless |
| **axis alignment** | fraction of detected straight segments within 2 degrees of horizontal or vertical | angles and wobble break blueprint semantics |
| **flatness** | fraction of pixels whose local gradient exceeds a threshold; unique quantised colours | gradients and shading, which the brief says to avoid and which tracing punishes |
| **structure facts** | a vision model answers the prompt's yes/no `structure` checks (counts, chained arrows, lane counts) | the diagram is decorative rather than diagrammatic |
| speed / VRAM / determinism / quant delta | identical to v1: load, cold first image, steady median/p95, peak VRAM at 250 ms, PNG-byte determinism, cross-quant SSIM | same reasons as the LLM track |

The correctness guard for this track is **OCR exact + structure checks**, with trace metrics as the quality
axis. A model that scores high on structure but traces into 1,500 paths is correctly reported as failing the
job, which is why the two are never collapsed into one number.

## Corpus

`prompts-diagrams.json`, frozen: **10 diagram prompts** (flowchart, floor plan, bar chart, org chart, schematic,
blueprint, binary tree, timeline, network topology, swimlane), **2 seeds**, = **20 images per arm**, each with
required `labels` and `structure` checks. Every prompt ends with the brief's own vector style suffix
(`flat 2D vector style, blueprint design, line art, sharp edges, solid monochrome fills, white background, high
contrast, no gradients, no shadows, no 3D`), so models are compared on the same forcing function the workflow
will use in production.

## Arms, priced and licensed at source (2026-10-20)

| arm | weights | download | licence / access |
|---|---|---|---|
| **FLUX.2-klein-4B** (recommended first) | GGUF Q4_K_M 2.60 + Q8_0 4.30 (quant pair) + `qwen_3_4b_fp4_flux2` 3.85 + `flux2-vae` 0.34 | **~6.8 GB** (+4.3 for the pair) | **Apache-2.0, ungated** |
| **FLUX.1-schnell GGUF** | `city96/FLUX.1-schnell-gguf` Q4_K_S **6.32** / Q5_K_S 7.70 / Q8_0 11.82 + `t5xxl_fp8_e4m3fn` **4.56** + `clip_l` 0.23 + flux VAE 0.32 | **~11.4 GB** at Q4_K_S | **Apache-2.0 but gated** (accept terms, token) |
| **FLUX.1-dev GGUF** | `city96/FLUX.1-dev-gguf` Q4_K_S **6.34** / Q5_K_S 7.72 / Q8_0 11.84 + same encoders | **~11.5 GB** at Q4_K_S | **non-commercial (`license: other`), gated** |
| **SD3.5 Medium** | FP8 checkpoint | ~5-6 GB + encoders | **`license: other` (Stability community), gated** |
| **Z-Image-Turbo** | int8 6.20 + encoder fp4 3.48 + ae 0.34 | **~8.3 GB** | Apache-2.0, ungated |

### The brief's headline FLUX configuration does not fit, arithmetically

It proposes **FLUX.1[dev] Q5_K_S + FP8 T5** as "entirely inside 12 GB with no system fallback". The real file
sizes say otherwise:

```
7.72 (Q5_K_S) + 4.56 (t5xxl fp8) + 0.23 (clip_l) + 0.32 (VAE) = 12.83 GB
card reports                                                   12,227 MiB = 11.94 GiB
```

That is over the card **before** activations, latents and the graph itself - so it spills, and the "30-50 s per
1024x1024 image" figure is being quoted for a configuration that cannot run the way it is described. The
admissible FLUX.1-dev configuration on this card is **Q4_K_S (6.34) + fp8 T5 (4.56) + VAE ≈ 11.45 GB**, which
still leaves only ~0.5 GiB, or Q5_K_S with the text encoder offloaded between stages (slower, and measurable).
This is the same class of error as our `--moe-cache-mib 6144` finding: a resource *request* is not an
allocation, and the only way to know is to measure peak VRAM.

Also worth stating plainly: **"FLUX.1 is the undisputed local champion" is a claim from a buying guide, not a
measurement.** It may well win on text rendering; FLUX.2-klein-4B is 4B, Apache-2.0, ungated and 6.8 GB, and
Z-Image-Turbo is newer and smaller. The trace metrics decide, not the blog.


## Three production methods, one corpus (added 2026-10-20)

The brief above lists three ways to get vector assets. They are **not alternatives for the instrument**; they
are **arms of it**, because the same frozen corpus and the same metrics can score all of them, and the
difference between them is exactly what we would be buying.

| method | what it produces | how it is scored here | local? |
|---|---|---|---|
| **A. diffusion + trace** | PNG, then SVG via tracer | full score: trace fidelity, path economy, axis, colours, OCR, structure | **yes** (FLUX/klein/Z-Image + vtracer) |
| **B. LLM writes SVG directly** | SVG source (`<path>`, `<rect>`, `<text>`) | same metrics **minus** trace fidelity (there is no tracing step): path/node counts read straight from the SVG, OCR and structure checks on a Chromium render | **yes** (any local coder model, no download) |
| **C. native vector models** (Recraft, Firefly) | SVG from a hosted model | would score well, but the run is **not reproducible offline**, is metered, and the weights are not inspectable | **no** |

**Why B belongs in the same protocol, not a different one.** Method B's geometry is mathematically exact, so it
should win on paths, nodes and axis alignment by construction - and its failure mode is different: text as
`<text>` depends on fonts at render time, layout breaks on crowded diagrams, and generated SVG can be
syntactically valid but visually empty. Those failures show up in the *same* OCR and structure checks, which is
what makes the comparison honest. Method B also costs **no model download**, so it can be measured today while
the diffusion arms wait.

**Why C stays out of the measured table unless Kevin says otherwise.** A cloud generator's output changes under
us, its cost is per image, and nothing about it can be re-run by someone reading the repository - which breaks
the one property this lab trades speed for. It can be listed as an **external reference row**, clearly marked
unmeasured-by-us, but it cannot sit in the same table as numbers we produced.

### The harness for method B, declared

Same `prompts-diagrams.json`, same seeds (ignored by B, recorded as such), same 20-prompt set. The model is asked
for **one complete SVG document, 1024x1024 viewBox, text as real `<text>` elements, no external fonts, no
gradients**, and nothing else. Its output is parsed (validity is itself a score), rendered in Chromium at a fixed
viewport, then scored with the identical pipeline. A model that returns prose, a broken document, or an SVG that
renders blank is **scored as failed, not excluded** - the same rule the LLM track uses for unscored cells.


## The didactic score: can a reader learn from it? (added 2026-10-20)

Every metric above measures the *asset*. The goal is **teaching**, so one more is needed: a diagram that
traces perfectly and labels correctly can still fail to explain anything.

**Method.** For each prompt, freeze **3 questions a learner should be able to answer from the image alone** -
not "is there a box labelled VALIDATE" (that is a structure check) but "after VALIDATE, what happens next?" and
"which step comes first?". Ask them of a vision model that sees **only the image**, no prompt text. Score
**answers correct / 3**. Same frozen questions for every arm, so the scorer's own weakness cancels between arms,
and the questions are written now, before any model runs, in `didactic-questions.json`.

Why this is the right instrument for "great teaching diagrams" and not a gimmick: it is the closest cheap
approximation to the only test that matters - a person looking at the image and understanding the thing. It also
catches a failure the other metrics actively reward: an image can have perfect OCR, perfect axis alignment and
zero contradictions while still being unusable, because an arrow points the wrong way or two boxes are ordered
ambiguously. **A didactically wrong diagram that traces beautifully is the most dangerous output in this track.**

Caveat, stated now: a vision model answering exam questions about a diagram measures *its* comprehension, not a
student's, and it can be fooled by text that simply repeats the answer. So the didactic score is reported
**beside** the structure checks, never as the sole verdict, and any arm that scores high here while failing the
structure checks is reported as suspicious rather than good.

## Toolchain (installs are small, GPU-free, and still a declared decision)

- **`vtracer`** (PyPI wheel, Rust tracer, `<2 MB`) - the raster-to-SVG step, fixed parameters recorded per arm.
- **`opencv-python-headless`** (~40 MB) - straight-segment detection for axis alignment, gradient statistics for
  flatness.
- **OCR**: `rapidocr-onnxruntime` (~90 MB with models, pip-only, offline) preferred over `pytesseract` (needs a
  system Tesseract binary we do not have). OCR must be **deterministic and local**, because it is part of the
  score.
- **Vision scoring** for `structure` checks: cloud DeepSeek V4.1 vision, frozen rubric, one yes/no per check, the
  same scorer for every arm so its bias cancels.
- **ComfyUI Desktop 0.8.27**, interpreter `.venv\Scripts\python.exe` (Python 3.12.11, torch 2.10.0+cu130, CUDA
  available, RTX 5070), invoked through `cmd.exe` because Git Bash refuses it.

## Order of work

1. **Install the scoring toolchain only** (no model weights): vtracer, opencv-headless, rapidocr. Validate on a
   synthetic diagram we draw ourselves, where the expected SVG path count is known - a tracer and an OCR that
   have never been checked against a known-good input are new variables, not instruments.
2. **FLUX.2-klein-4B Q4_K_M** first: ungated, Apache-2.0, smallest real download, and it gives the quant pair
   (Q4 vs Q8) for free once the Q8 file is fetched.
3. **FLUX.1-schnell Q4_K_S** next, since it is Apache-2.0 and needs only access approval; **FLUX.1-dev** only if
   its non-commercial licence is acceptable for the intended use.
4. **SD3.5 Medium** as the "more headroom, weaker adherence" comparison; **Z-Image-Turbo** if the flat-2D
   metrics favour it.

Each arm gets a prediction before it runs: expected OCR exact rate, expected trace fidelity, expected path
count band, expected latency, and the specific observation that would make me distrust the number.
