# Image-model instrument: teaching diagrams that must survive vectorisation

This folder is a **measurement instrument plus its evidence**, not a library and not a demo. It holds

- a **frozen corpus** of diagram tasks, declared before any model ran;
- a **deterministic scoring toolchain** (tracer, OCR, structural checks, geometry assertions) that needs no
  network and no GPU;
- the **artifacts and result files** of every arm run on 2026-10-20/21, and one prose RESULT file per experiment
  recording what was measured, what was predicted, and what went wrong with the measurement.

The question it exists to answer: **which local method can produce charts, blueprints, floor plans and
schematics whose geometry survives a raster-to-vector tracer and whose labels a reader can actually read?**
"Looks clean" is an opinion; "traces cleanly and every label is verbatim" is a number.

## Read in this order

1. [PLAN-20261021.md](PLAN-20261021.md) — the five work packages that maintain this folder, and the boundary they
   inherit (no model runs, no GPU, no network in maintenance work).
2. [PROTOCOL-2D.md](PROTOCOL-2D.md) — the current protocol: metrics, the frozen diagram corpus, the arms, and the
   didactic score. [PROTOCOL.md](PROTOCOL.md) is v1 and is superseded by it.
3. [PROTOCOL-CAD.md](PROTOCOL-CAD.md) — methods D and E, the subset rule that keeps cross-arm comparison honest,
   and the metrics that separate exact-geometry arms from traced ones.
4. [INSTRUMENTS.md](INSTRUMENTS.md) — one section per script: what it measures, its unit, good and bad values,
   and the calibration error it has already had.
5. [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — the seventeen measurement errors of 2026-10-20/21, each with its fix
   and the RESULT file that records it. Read this before you change a threshold.

## The five methods, and what is actually measured

| method | what it produces | scored on | state |
|---|---|---|---|
| **A** diffusion + trace | PNG, then SVG by `vtracer` | trace metrics, OCR, structure, didactic | FLUX.2-klein-4B, FLUX.1-schnell and Z-Image-Turbo measured; FLUX.1-dev **blocked** |
| **B** LLM writes SVG | editable SVG with real `<text>` | path/node counts from source, OCR and structure on a Chromium render | measured, and the best local answer for this job |
| **C** hosted native-vector models | SVG from a cloud model | — | **not measured by us**: not offline-reproducible |
| **D** Blender `bpy` | 3D scenes, animation | animation-readiness | **never run**: not installed ([PROTOCOL-CAD.md](PROTOCOL-CAD.md)) |
| **E** KiCad `pcbnew` | true vector, real text | validity, editability, labels — **from source** | measured on three prompts |

Method B is at the ceiling on axis alignment (100 %) and label completeness in source (70/70 after one repair
round, 19/19 documents), and diffusion is ahead only on raster-trace path economy, where B's number is an artefact
of re-tracing an SVG that never needed tracing. The numbers and their denominators are in
[RESULTS-METHOD-B.md](RESULTS-METHOD-B.md), [RESULTS-ARMS-20261020.md](RESULTS-ARMS-20261020.md) and
[RESULTS-FOUR-ARMS.md](RESULTS-FOUR-ARMS.md).

## Frozen corpora

All of these carry `"frozen": true` and were fixed before the corresponding runs. Changing one invalidates the
comparison it belongs to; add a new file instead.

| file | contents | used by |
|---|---|---|
| `prompts.json` | **18** prompts in 6 check categories (text, count, spatial, attribute, negation, scene) | protocol v1 only, superseded |
| `prompts-diagrams.json` | **10** diagram prompts × 2 seeds = **20** images per arm, each with required `labels` and yes/no `structure` checks | every arm |
| `didactic-questions.json` | 3 learner questions per prompt, frozen before any run; score = correct / 3 | `vlm_score.py` |
| `structure-expectations.json` | `<field>_exact` / `<field>_at_least` / `label_multiplicity` per prompt, with the reason for each revision | `validate_structure.py`, `validate_labels_multiplicity.py` |
| `shape-rules.json` | shape proxies for the five free-form prompts (`process-1`, `blueprint-1`, `circuit-1`, `floor-1`, `tree-1`) | `validate_shape.py` |
| `hard-task-pooltable.json` (+`-v2`, `-v3`, `-v4`) | the pool-table task, deliberately outside the 10-prompt corpus, with the app's own constants in v3/v4 | `hard_arm*.py`, `hard_v*.py` |

The ten-prompt corpus asks for labels, counts and simple relations. The pool-table task asks for a proportion, a
triangular arrangement and an angle at once, which is why it separated the methods on different axes.

## Running the toolchain

**The interpreter quirk.** `.venv` is gitignored, so it exists only in the main checkout — a `git worktree` does
**not** have one. Call the interpreter by absolute path, and call it through `cmd.exe`: Git Bash reports
`command not found` for `.venv\Scripts\python.exe` even when the file exists and is executable.

```
MSYS_NO_PATHCONV=1 cmd.exe /c "C:\Users\kevin\ai_bench\image-models-20261020\.venv\Scripts\python.exe <script> <args>"
```

Installed and sufficient for everything in `INSTRUMENTS.md` that does not run a model: `numpy`,
`opencv-python-headless`, `scikit-image`, `vtracer`, `rapidocr-onnxruntime`, `pillow`.

**Score something. Nothing below needs a GPU or a network.**

```bash
# 1. positive control: a diagram whose properties are known (4 boxes, 3 connectors, 4 labels, 2 colours)
python make_synthetic.py synthetic-flow.png
python trace_score.py synthetic-flow.png

# 2. negative control: the shape the protocol penalises (gradient explodes into overlapping paths)
python trace_score.py synthetic-gradient.png

# 3. one folder of renders, end to end: trace metrics plus OCR against the corpus's required labels
python score_arm.py render/zimage result-arm-zimage-scores.json

# 4. structural, shape, topology, door and label checks on method B's SVG source
python validate_structure.py --model qwen3-coder:30b
python validate_shape.py --model qwen3-coder:30b
python validate_topology.py --model qwen3-coder:30b
python validate_doors.py --model qwen3-coder:30b
python validate_labels_multiplicity.py qwen3-coder:30b

# 5. geometry: either the 1024-unit checks, or the app's own constants in its own coordinate system
python check_geometry_v2.py hard/pooltable-v2-bare.svg
python check_ace_geometry.py hard/pooltable-v3-bare.svg hard/pooltable-v4-bare.svg
python check_containment.py hard/pooltable-qwen3-coder_30b.svg
```

**What needs a model (not part of maintenance work).** `svg_arm.py` and the `hard_*.py` generators need a local
Ollama; `klein_arm.py`, `flux_arm.py`, `zimage_arm.py` and `generic_arm.py` need ComfyUI and its weights;
`cad_arm.py` needs KiCad 10's own Python; `vlm_score.py` needs `DEEPSEEK_API_KEY`. Those were run once, serially,
on 2026-10-20/21 and their output is what the RESULT files record.

## Where the evidence lives

- **`RESULTS-*.md`** — one per experiment: the table, the predictions, the corrections, and what the run does *not*
  establish. These are the citations `INSTRUMENTS.md` and `TROUBLESHOOTING.md` point at.
- **`result-*.json`** — the per-image and per-document rows behind those tables. Every summary number in a RESULT
  file can be recomputed from these.
- **`svg/qwen3-coder_30b/`** and **`svg/qwen3-coder_30b-repaired/`** — method B's artifacts, before and after one
  label-repair round. This is the primary evidence for the code arm.
- **`hard/`** — the pool-table SVGs for every prompt version and configuration.
- **`kicad/`** — boards, exports and the model-written `pcbnew` scripts for method E.
- **`render/`** — the rendered PNGs the raster metrics consume. It is gitignored and therefore **absent from a
  fresh clone**; without it, `score_arm.py` and the diffusion tables cannot be recomputed, though every number in
  them is still present in `result-*.json`.

## What has been established

- The scoring toolchain **discriminates**: on known inputs it separates a clean diagram (37 paths, 2 major colours,
  100 % axis) from a gradient (164 paths, 30 colours, 11.9 % straight coverage), and it recovers a synthetic
  flowchart's ground truth.
- **Text fidelity is the discriminator for teaching diagrams**, and the best local code arm beats every diffusion
  arm on it: 0.876 by OCR, 0.971 by source, **70/70** labels after one repair round with no new weights.
- **The failure modes are different in kind**, which is the useful result: method B omits or duplicates a label and
  still draws exact geometry; method E fails by not running at all; diffusion misspells inside a requested word and
  is not deterministic at fixed seed.
- **A checker can pass a wrong diagram.** The v4 pool table scored 10/10 on geometry while the cue pointed at the
  rack; the eleventh check names it. The render is still looked at, not just the score.
- **Most instruments in this folder were wrong before a model was.** Seventeen times, all recorded in
  [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## What has not been established

- **Rates.** n = 1-2 per prompt, one model per arm, one temperature, one quantisation per diffusion model, one
  sampler. These are shapes, not rates, and differences under a few points carry the arms' own noise.
- **Determinism.** None of the diffusion arms is deterministic at fixed seed (PNG hashes differ between identical
  runs), so small cross-arm deltas are not effects.
- **The didactic score** measures a vision model's comprehension of the image, not a student's, and can be fooled by
  text that repeats the answer. It is never a sole verdict ([PROTOCOL-2D.md](PROTOCOL-2D.md)).
- **FLUX.1-dev** — the configuration the briefs' "champion" claim rests on — is **blocked, not measured**: it blurs
  at 1024x1024 in this stack and is sharp at 512, so the arm is reported unscored rather than as a zero
  ([RESULTS-DEV-BLOCKED.md](RESULTS-DEV-BLOCKED.md)).
- **The VRAM floor** is a distribution, not a number: Z-Image-Turbo was marginal at 1024 and passes at 512, so any
  floor claim here names its resolution ([RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md)).
- **Blender (method D) was never run**, and no animated asset has been scored.
- **Two dimensions the prompts ask for are recorded as unmeasurable**, not passed: dimension arrows on blueprints,
  and lane semantics (a connector proves it spans the divider, not that it means what the diagram claims).

## Generating with a window instead of a command line

```powershell
.\start-comfy.ps1                  # session server, optional - the UI will start one itself
python zimage_ui.py                 # prompt box left, picture right, progress bar between
```

`zimage_ui.py` keeps the server under the window's control: it starts ComfyUI if nothing is answering and stops it on
close, but leaves a server it did not start alone. Enter generates, Shift+Enter adds a newline. Step progress comes from
ComfyUI's websocket (`/ws`) through a minimal RFC6455 client, because no websocket package is installed here.

**Stop** halts a run so parameters can be changed without waiting: it POSTs ComfyUI's `/interrupt`, clears the queue,
and the worker stops waiting. Measured response 0.2 s; the server stays up, and the next run works immediately. A
sequential N-up keeps the tiles it already wrote (the status line says how many), so stopping mid-grid is not a total
loss.

Controls: **steps** (2-12; turbo is distilled for 4), **sampler** (euler or euler_ancestral - the latter injects noise
per step, which adds grain and is the real "randomisation" knob), **strength** for img2img, and **seed+1 per tile** (an
N-up runs one image at a time with successive seeds so each tile is individually reproducible; unticked asks for one
batch, about 8x faster: 6 s vs 50 s for 9-up at 256 px).

**Guidance (CFG) is deliberately not exposed**, because it was measured and removed: on this turbo model raising it from
1.0 to 1.5 halves edge energy (3.91 -> 1.92) while OCR still reads the same 4/4 labels, so it buys no adherence and costs
crispness. CFG stays at the model's distilled 1.0.

Size and layout are chosen in the toolbar: sizes 256-1024, and **single / 4-up (2x2) / 9-up (3x3)**. An N-up run asks
ComfyUI for one batch, saves every tile separately, and composes the tiles into a single contact sheet which is what the
window shows and what the gallery lists (`..._sheet_9up.png`). A base image forces single, because an edit is one image.

Every image is saved with a **sidecar JSON** (`<same name>.json`) recording the prompt, seed, size, steps, cfg, model,
and - for an edit - the **absolute path of the base image and the strength used**. Selecting a row in the gallery
restores that prompt and seed into the boxes, and *Copy image* puts the PNG on the Windows clipboard.

Flags: `--port` (8006), `--size` (1024), `--steps` (4), `--cfg` (1.0), `--seed` (1234), `--out`
(`C:/Users/kevin/Pictures/zimage-ui`), `--no-autostart`, `--no-progress`, `--initial TEXT`, `--initial-file PATH`, `--auto`.

On Windows, prefer `--initial-file` for a long or punctuated prompt: PowerShell's `Start-Process -ArgumentList`
splits a multi-word argument into several, so `--initial "a blue circle"` arrives as `--initial a` plus strays.

## Licence

**FSL-1.1-MIT** — see `LICENSE.md`. The scripts, harness, prompts and result documents here are licensed under the
Functional Source License 1.1 with an MIT future licence; it converts to MIT after two years.

Model weights and the third-party tools this work measures or drives (llama.cpp, ComfyUI, KiCad, Ollama, and the
diffusion and language models themselves) are **not** covered and are **not** distributed here — they remain under
their own licences. Nothing in this repository redistributes a model.
