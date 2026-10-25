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

## Deploying the weights (what a fresh machine needs)

Nothing here packs weights; every file below is fetched from its own repo under its own licence. Three pieces of
plumbing must exist before any profile works:

1. **ComfyUI** (the desktop build here is 0.8.27) with the **ComfyUI-GGUF** custom node. Both `UnetLoaderGGUF` and
   `CLIPLoaderGGUF` come from that node, and the GGUF text encoders are only visible to it (it registers a
   `clip_gguf` folder over `text_encoders`).
2. **`extra_models_config.yaml`** (in `%APPDATA%/ComfyUI/`) pointing at the weights volume - here
   `D:\models\comfyui` with `diffusion_models`, `text_encoders`, `vae`, `loras`, `checkpoints`. Without it the server
   starts and answers `/system_stats` but every loader rejects its own files with `value_not_in_list ... not in []`.
3. **The server launched with those flags**: `--base-directory C:\Users\kevin\Documents\ComfyUI
   --extra-model-paths-config %APPDATA%\ComfyUI\extra_models_config.yaml`. `start-comfy.ps1` does this.

| profile | files (folder) | size | source |
|---|---|---|---|
| `flux2 klein`, `flux2 klein uncensored` | `flux-2-klein-4b-Q4_K_M.gguf` (diffusion_models), `flux2-vae.safetensors` (vae) | 2.6 + 0.31 GiB | unsloth FLUX.2-klein-4B-GGUF, Comfy-Org Flux-2-Klein-4B |
| | shipped encoder `qwen_3_4b_fp4_flux2.safetensors` | 3.58 GiB | Comfy-Org Flux-2-Klein-4B |
| | uncensored encoder `flux2-klein-4b-uncensored-q4_k_m.gguf` | **2.33 GiB** | `nerualdreming/flux2-klein-4b-uncensored-text-encoder` (ungated) |
| `flux2 klein 9b uncensored` | `flux-2-klein-9b-Q4_K_M.gguf` (diffusion_models) | **5.50 GiB** | `unsloth/FLUX.2-klein-9B-GGUF` (ungated, FLUX non-commercial) |
| | `flux2-klein-9b-uncensored-te-q5_k_m.gguf` (text_encoders) | **5.45 GiB** | `wy8edhuh/flux2-klein-9b-uncensored-text-encoder-Q5_K_M-GGUF`, a re-quant of `ponpoke`'s Qwen3-8B |
| **`Make clip`** (Wan 2.2 TI2V-5B) | `Wan2.2-TI2V-5B-Q4_K_M.gguf` (diffusion_models) | **3.20 GiB** | `QuantStack/Wan2.2-TI2V-5B-GGUF` (apache-2.0) |
| | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (text_encoders) | **6.27 GiB** | `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` (apache-2.0) |
| | `wan2.2_vae.safetensors` (vae) | **1.31 GiB** | the same repo |
| `z-image turbo` | `z_image_turbo_nvfp4.safetensors`, `qwen_3_4b_fp4_mixed.safetensors`, `ae.safetensors` | ~10 GiB | Comfy-Org z_image_turbo |
| `flux1 schnell` / `flux1 dev` | `flux1-*-Q4_K_S.gguf`, `t5xxl_fp8_e4m3fn.safetensors`, `clip_l.safetensors`, `flux1-ae.safetensors` | ~11.4 GiB each | city96 FLUX.1 GGUF repos |

Verify a deployment in two calls, before blaming a prompt:

```bash
curl -s http://127.0.0.1:8006/object_info/CLIPLoaderGGUF | python -c "import json,sys; print(json.load(sys.stdin)['CLIPLoaderGGUF']['input']['required']['clip_name'][0])"
curl -s http://127.0.0.1:8006/object_info/UnetLoaderGGUF | python -c "import json,sys; print(json.load(sys.stdin)['UnetLoaderGGUF']['input']['required']['unet_name'][0])"
```

If a profile's encoder is missing from the first list, the file is in the wrong folder or the extra-paths config is
not loaded - the graph will be rejected rather than silently falling back. `--profile` lists the wired profiles and
`zimage_ui.py` offers the same list in its model picker.

## Making a clip from a still

**Make clip (2 s)** under Generate turns the picture on screen into two seconds of motion with **Wan 2.2 TI2V-5B**
(5B, apache-2.0, GGUF Q4_K_M). Three things are enforced rather than documented:

- **The image model is unloaded first.** `video.py` posts `/free {"unload_models": true, "free_memory": true}` before
  submitting, because the encoder (6.27 GiB) and the diffusion model (3.20 GiB) are loaded one after the other and the
  card must hold the larger of the two, not both plus a resident image model.
- **The still is cropped to a grid the model was trained on** - 704x704, or 1280x704 for a wide picture, multiples of
  32. A 1024x1024 still becomes 704x704: 47 % of the pixels, which is also 47 % of the compute. Stretching to fit is
  what produces the warped-motion look.
- **You know it is done when the status line changes.** The bar is determinate and the websocket reports
  `step x of 30` while the sampler runs, so it is not a spinner; at the end the status reads `clip done: <file>`, the
  hint gives frames, seconds, wall time, peak VRAM and where the file is, and the clip **opens by itself** in the
  default player. **Open clip** reopens the last one without rendering again.
- **The deliverable is an MP4**, not the WebP: ffmpeg on this box lives in WSL and its own WebP decoder rejects the
  animated file ComfyUI writes, so `video.py` decodes the frames with PIL, writes a PNG sequence and encodes H.264 from
  that. The WebP is kept beside it as the direct model output. The clip does **not** appear in the gallery, which lists
  PNG stills only.
- **Frame count is 4k+1** (49, 81, 121 frames = 2.0, 3.4, 5.0 s at 24 fps), which is what the model's temporal
  compression accepts. `video.py` refuses anything else instead of rounding silently.

Measured on this box, from a 512 px still, 30 steps at cfg 5.0, uni_pc, 24 fps:

| run | wall | per frame | peak VRAM | free | MP4 encode | result |
|---|---|---|---|---|---|---|
| 49 frames, 704x704 (2.0 s) | **109.7 s** | 2.2 s | 9,732 MiB | 2,495 | **2.8 s** | 6.7 MiB WebP + 1.0 MiB MP4 |
| 121 frames, 1280x704 (5.0 s) | **699.4 s** | 5.8 s | **11,913 MiB** | **314** | **17.5 s** | 18.7 MiB WebP + 2.8 MiB MP4 |

Two things that table says plainly. **The 2-second clip fits; the 5-second 16:9 one does not fit comfortably** - 314 MiB
free is under this lab's 0.5 GiB floor, so 704x704 (or 1024x704) is the honest grid here and 1280x704 at 121 frames is
a floor breach recorded rather than hidden. And **combining the frames is not the slow part**: MP4 encoding is 2.8 s of
109.7 s and 17.5 s of 699.4 s, about 2.5 % either way. The waiting is the sampler.

Motion checked the only way worth checking: frame 24 differs from frame 0 by 26.6 mean absolute error with 63 % of
pixels moved, the bicycle stays coherent across the clip - no warping, no limb drift. MP4s verified with ffprobe:
704x704 / 24 fps / 49 frames / 2.04 s, and 1280x704 / 121 frames / 5.04 s.
CLI equivalent: `python video.py --still <png> --grid 16:9 --frames 81 --prompt "the camera drifts left"`.

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

The **seed** field has minus / plus buttons and a **Rnd** button (0-999,999,999); the buttons clamp at 0 and fall back to
the default seed if the box holds something non-numeric.

The seed **randomises itself after every generation**, so pressing Enter twice gives two different images rather than
the same one twice; the new value is guaranteed to differ from the old so the change is always visible, and the hint
line reports it (`next seed 813589306`). The exception is an edit you armed with **Use as Base**: that picture's seed
comes back with it and stays for the run, so an edit is reproducible (`seed 555 kept`). A plain click in the gallery
restores the seed so you can read or reproduce the picture, but the next generation still randomises, because looking at
an image is not editing it.

Controls: **steps** (2-12; turbo is distilled for 4), **sampler** (euler or euler_ancestral - the latter injects noise
per step, which adds grain and is the real "randomisation" knob), **strength** for img2img, and **one at a time
(seed+1)**, which is the default: an N-up is N separate requests with seeds seed..seed+N-1, so every tile is
individually reproducible and the VRAM peak stays that of a single image. Unticking it asks ComfyUI for one batch,
about 8x faster (6 s vs 50 s for 9-up at 256 px) with a much higher peak.

**rephrase** sits directly under the prompt box and rewrites it with synonyms - no model, no server, so it always works.
The dictionary is **947 words**, from two layers:

| layer | words | where it comes from |
|---|---|---|
| generated | 866 | `build_synonyms.py` parses WordNet 3.0 synsets, restricted to visual vocabulary |
| hand-written | 181 | `VISUAL_EXTRA` + `SYNONYMS` in `rephrase.py`, and these win on conflict |

WordNet alone is not usable here: its noun senses are prose-shaped, so it pairs *red* with **loss** (in the red), *mug*
with **fool** (a gullible person) and *dog* with **track, tag**. The builder therefore keeps only replacements from a
visual allow-list (`COMMON` in the builder - colours, materials, light, weather, subjects, poses, textures), which drops
those senses without hand-checking thousands of entries. Where WordNet has nothing at all - *brick*, *kitchen*, *window*,
*red* - the hand-written layer covers it. Regenerate with:

    python build_synonyms.py --senses 4        # downloads WordNet once, writes data/synonyms.json (~25 KB)

WordNet 3.0 data is Princeton's; the licence travels with the output in `data/WORDNET-LICENSE.txt`. Each press re-derives from the *original* wording and walks the next variant, so
three presses give three different phrasings rather than converging; editing the text starts the cycle again. **Your
own wording is part of that cycle, not excluded from it**: a word with *n* alternatives comes back round to itself
after *n + 1* presses, so "bicycle" walks bike, cycle, pushbike and then returns "bicycle". A whole sentence returns at
the lowest common multiple of its words' cycles - 4 presses for "a red bicycle", 60 for a long one - and the hint line
names that number instead of leaving you to discover it ("your own wording returns in 12 presses"). Whole
words only, capitalisation carried over, and repeated words collapsed, so "leaning against a wall" cannot become
"propped against against". It reports how many words moved ("rephrased 7 words"), and says so plainly when a prompt has
no known synonyms instead of pretending it did something.

Every control carries a one-line tooltip, and a **banner** above the progress bar states what the next Generate will
do before it is pressed: `TEXT → IMAGE · 4 steps · euler · 512 px · one image · seed 1234`, switching to
`EDIT img.png at 0.60` when a base is set. Choosing a layout prints its measured cost (`16 tiles · ≈ 3 m 39 s one at a
time, or ≈ 11 s as one batch`), from a table of timings taken on this machine rather than a formula.

Window size, steps, sampler and layout are remembered in `%LOCALAPPDATA%/zimage-ui/settings.json`, and Up/Down in the
prompt box recalls previous prompts. The *chain* box is deliberately **not** remembered, so a fresh window always starts
as text to image.

**What sends an image at all** is the base, and only three gestures set one: the **Use as Base** button, the **chain**
box after a run, and **Ctrl+V** (paste from the clipboard). A plain click in the gallery **looks** - it shows the
picture and restores its prompt and seed, and the banner stays on `TEXT → IMAGE`. That distinction is the fix for a
measured annoyance: the click used to set the base as a side effect, so a run could upload an image while the old
"reuse last image as base" box was unticked, and nothing on screen tied the two together. Use as Base names the file
in the base row, switches the banner to `EDIT <file> at 0.60`, and the hint line adds how old the picture is
(`made 6h 51m ago`) so a base picked hours earlier cannot be sent silently. **Clear Base** puts it back to text to image.

**chain: each new image becomes the next base** is off by default. Unticked, the picture that just appeared does
**not** become the base, so pressing Enter again produces another fresh image. Ticked, each finished image becomes the
base for the next run and the status line says which file is now the base.

**Guidance (CFG) is deliberately not exposed**, because it was measured and removed: on this turbo model raising it from
1.0 to 1.5 halves edge energy (3.91 -> 1.92) while OCR still reads the same 4/4 labels, so it buys no adherence and costs
crispness. CFG stays at the model's distilled 1.0.

Size and layout are chosen in the toolbar: sizes 256-1024, and **single / 4-up (2x2) / 9-up (3x3) / 16-up (4x4)**.
One at a time is the default; a single batch is the opt-in. Measured sequential 4-up at 256 px: 26.6 s (6.65 s per
tile), peak 5,508 MiB - a single-image peak, and four separately named tiles. Measured as one batch, 16-up at 384 px:
11.3 s total (0.71 s per image), peak 11,661 MiB of 12,227, so 16-up in one batch is usable at 256-448 and not above
that on a 12 GB card - and the UI now warns only in that batch case, since one at a time does not raise the peak.

Each tile appears in the gallery **as it finishes**, not when the run ends - the strip is refreshed on every `tile`
event, so a 16-up fills in as it goes and a new image is selectable while the rest are still rendering. Measured on a
live 4-up at 256 px: gallery counts 1, 2, 3 at 5.9 s, 12.0 s and 17.9 s, then 5 when the sheet landed at 30.3 s.

Either way an N-up saves every tile separately and composes them into a single contact sheet, which is what the window
shows and what the gallery lists (`..._sheet_9up.png`). A base image forces single, because an edit is one image.

Every image is saved with a **sidecar JSON** (`<same name>.json`) recording the prompt, seed, size, steps, cfg, model,
and - for an edit - the **absolute path of the base image and the strength used**. Selecting a row in the gallery
restores that prompt and seed into the boxes, and *Copy image* puts the PNG on the Windows clipboard.

Flags: `--port` (8006), `--size` (1024), `--steps` (4), `--cfg` (1.0), `--seed` (1234), `--out`
(`C:/Users/kevin/Pictures/zimage-ui`), `--no-autostart`, `--no-progress`, `--initial TEXT`, `--initial-file PATH`, `--auto`.

On Windows, prefer `--initial-file` for a long or punctuated prompt: PowerShell's `Start-Process -ArgumentList`
splits a multi-word argument into several, so `--initial "a blue circle"` arrives as `--initial a` plus strays.

### Styles, models, and verbs on a picture

The window is no longer only prompt-shaped. Fooocus's split between a small surface and an "Advanced" drawer is adopted
directly: **style** and this picture's actions stay visible, while *model*, *steps*, *sampler* and *negative prompt* live
behind **Advanced**.

Styles are prompt suffixes (`STYLES` in the module) - the model is not retrained, words are appended: photo, icon,
diagram, blueprint, watercolour, cinematic. The suffix applied is written into the sidecar as `style`, and it is applied
**once**: `_apply_style` leaves a prompt that already ends with its suffix alone, and every restore path (Vary, hires, a
gallery click) puts the *unsuffixed* prompt back in the box, so rerendering cannot stack
"photograph, natural light, shallow depth of field" three deep.

`repair_prompts.py` fixes sidecars written before that: dry run by default, `--apply` writes after copying every file it
touches into `.prompt-backups/`. It kept the user's edited words that had landed *between* two suffixes - of 717 sidecars,
56 needed repair and the worst had the suffix three times mid-sentence.

**model** sits in the always-visible row next to *style* - it is a first-class choice, not an Advanced secret, and the
hint says what switching costs ("the first image loads that checkpoint, about 10-30 s"). Six profiles, each verified
against ComfyUI's own node list:

**The defaults are the measured pose combo: flux2 klein at strength 0.70.** That pairing is the only one in the sweep
that both seated the figure and kept her face from denoise 0.70 upward, at 7-8 s and 5.7 GiB - lighter than the
previous default (z-image, 6.7 GiB) and quicker to edit. The trade-off is honest: for pure text-to-image z-image turbo
is still the faster choice (5.6 s against ~8 s), so it is one click away in the model picker. Both are remembered per
install; strength is on the slider and persisted alongside the model.

| profile | weights | encoder | measured at 256 px, 4 steps |
|---|---|---|---|
| z-image turbo | `z_image_turbo_nvfp4` (UNETLoader) | qwen_3_4b (lumina2) | **7.4 s** |
| flux1 schnell | `flux1-schnell-Q4_K_S.gguf` (UnetLoaderGGUF) | clip_l + t5xxl (flux) | **18.8 s** |
| flux2 klein | `flux-2-klein-4b-Q4_K_M.gguf` (UnetLoaderGGUF) | qwen_3_4b_fp4_flux2 (flux2) | **9.8 s** at 512 px, 4 steps, cfg 1.0 |
| flux2 klein uncensored | the same `flux-2-klein-4b-Q4_K_M.gguf` | `flux2-klein-4b-uncensored-q4_k_m.gguf` (flux2, **CLIPLoaderGGUF**) | 7.1 s vs 13.3 s cold, 512 px, same seed |
| flux2 klein 9b uncensored | `flux-2-klein-9b-Q4_K_M.gguf` (UnetLoaderGGUF) | `flux2-klein-9b-uncensored-te-q5_k_m.gguf` (flux2, **CLIPLoaderGGUF**) | **32.5 s** median, 8.8 GiB peak |
| flux1 dev | `flux1-dev-Q4_K_S.gguf` (UnetLoaderGGUF) | clip_l + t5xxl (flux) | **27.0 s** at its own 20 steps, cfg 3.5 |

**flux2 klein uncensored is an encoder swap, not a different diffusion model.** The diffusion GGUF, the VAE, steps and
cfg are byte-identical to `flux2 klein`, so an A/B isolates the text encoder. The encoder is a Qwen3-4B language model
in GGUF form (36 blocks, `blk.*` tensors, tokenizer metadata embedded - verified by reading the file, not the repo
name), which is why a profile can name `CLIPLoaderGGUF` and nothing else in the graph changes. Source:
`nerualdreming/flux2-klein-4b-uncensored-text-encoder` q4_k_m, 2.33 GiB, ungated; the richer gated `ponpoke` repos
(bf16 7.49 GiB, and the 9B/Qwen3-8B variant) need the terms accepted on the account first.

The negative prompt is a real node input now (node 5 was hard-coded empty), and cfg follows the profile rather than
being pinned at 1.0. Picking a model also applies that model's step count - flux dev at the z-image default of 4 steps
would look broken, so the switch sets 20 and says so in the hint line.

Under the picture, three verbs and two editors:

| control | what it does | measured |
|---|---|---|
| **Vary** | same prompt from the sidecar, seed + 1 | - |
| **Subtle** | re-samples the picture itself at strength 0.35 | - |
Editing honours the size selector: an img2img or inpaint request **resizes the base to the chosen size** (centre
crop, so the aspect is kept) instead of inheriting the base's dimensions. Measured: a 256 px base asked for 512
returned 512x512 in 7.0 s, asked for 384 returned 384x384 in 6.7 s, and an inpaint with a 256 base and 256 mask at
384 returned 384x384 in 8.0 s - the mask travels through the same resize (MaskToImage -> ImageScale -> ImageToMask)
so it still lines up.

| **hires x2** | real Hires. fix: render at the current size, then `LatentUpscaleBy` and a second pass at denoise 0.45 | **7.8 s** for 384 to 768 px |
| **paint mask -> Fill selection** | the brush lives on the picture: paint, and the Generate button becomes *Fill selection*; only painted pixels pass through `VAEEncodeForInpaint` | **8.9 s** on a 1040 px base |
| **expand** | the pad arrows grow one side, the button becomes *Expand* via `ImagePadForOutpaint` | **11.6 s**, 256 to 384 px |
| **pin / compare** | pin any picture, then drag the split against whatever is on screen - not only against the base | - |

Generate is one large green button across the bottom of the prompt column, with **Clear base** immediately above it at
the same width and height (grey, disabled until a picture is the base), so the commit key is where the eye ends up after
reading the prompt and its companion action sits with it. It renames itself to *Fill selection* or *Expand* when the
picture has a mask or pads. It shows no timing guess - the line under it reports what the **last run actually took**
(`last image 6.7 s · 512 px · 4 steps · z-image turbo`, or `last run 30 s · 16 tiles, 2 s each`), measured per image
rather than estimated. The estimate survives only in the layout-change hint, where it is explicitly a forecast, so the price is visible
before the click, and the banner carries the memory: `VRAM ≈ 8.7/11.9 GiB` in red when a shape comes within 400 MiB of
the card. Both come from peaks measured here with `nvidia-smi` at 0.25 s:

| shape | peak | wall |
|---|---|---|
| z-image single 512 | 6,708 MiB | 7.6 s |
| z-image single 1024 | 7,193 MiB | 15.1 s |
| z-image 16-up, one at a time, 384 | 6,507 MiB | 160.3 s |
| z-image 16-up as one batch, 384 | 8,206 MiB | 15.3 s |
| z-image 16-up as one batch, **512** | **8,927 MiB** | 18.5 s |
| z-image hires ×2 at 512 | 7,013 MiB | 13.5 s |
| flux1 dev single 512 | 9,458 MiB | 40.3 s |

That last-but-one row retracts an earlier claim in this file: the window used to warn that 16-up above 448 px "needs
more than a 12 GB card". It does not - the measured peak is 8,927 of 12,227 MiB and the run completed. The warning was
replaced by the estimate, and a model with no measured peak (flux1 schnell) shows no number rather than a borrowed one. **graph** shows the exact ComfyUI workflow the last run sent, copyable - the simplest way to see what
the window actually did. **enhance** and **Describe** are disabled with a reason until a local OpenAI-compatible
endpoint answers at `llm_url` (probed once at startup, never off-machine).

**Every tile of an N-up gets its own seed** - `seed`, `seed+step`, `seed+2*step` - and **seed step** (Advanced, default
1) widens the stride when +1 is not enough spread. A *single batch* cannot do this: one latent batch is drawn from one
seed, so its tiles share it, and the hint says so when you untick *one image at a time*. Measured on a 4-up at 256 px,
step 1: seeds 500/501/502/503 and pairwise mean absolute differences of 58-84, i.e. genuinely different pictures.

**Sweep** (Advanced) changes one parameter across the tiles of an N-up - `cfg`, `steps` or `seed` - so a 16-up sheet
becomes a measured comparison rather than 16 guesses; the values can be typed (`1,2,3` or `1..4x6`) instead of taking
the default spread. Each tile records its own value in the sidecar; a measured 4-up
sweep wrote cfg 1.0/2.0/3.0/4.0 in 28 s. **Preview** renders 2 steps at 256 px into `.previews/` without touching the
gallery, for when 14 s is too long to wait on a guess. **save style** stores the current prompt as a reusable style.
**Describe** captions the current picture through the same local endpoint the enhancer uses. Saved styles, favourites
and the run log persist in the settings file.

`Ctrl+V` pastes an image from the clipboard as the base. **Run log** lists this session's runs with seed, model, style
and wall time. The strip gained a search box, a **star** (favourites float to the top and persist) and a *★ only* filter.
**Enhance** rewrites the prompt with a local OpenAI-compatible endpoint (`llm_url`, default
`http://127.0.0.1:8080/v1`) and refuses any non-loopback host.

### Gallery: multiselect and delete

The **Recent (18)** strip shows the newest 18 images as a grid of 92 px tiles, 3 across and 6 deep, and marks them for
action instead of only loading them:

| gesture | effect |
|---|---|
| click | selects that image, loads it, makes it the base, restores prompt and seed |
| **ctrl+click** | adds or removes that image from the selection, leaving the base alone |
| **shift+click** | selects the range from the last clicked image to this one |
| **Select all** | takes the whole strip; pressing it again clears |
| **Delete (N)** | confirms, then moves the PNG, its sidecar and its thumbnail to the **Recycle Bin** (recoverable) |
| **Del / Ctrl+A / Esc** | delete selection, select all, clear selection - ignored while the prompt box has focus |

Two states are visible at once and do not conflict: a **blue ring** means "selected", a **black border** means "loaded
as the base", so one tile can be both. Deleting the base clears it and resets the picture pane. The **More…** window
shares the same selection and carries its own Delete, so a sweep can span both.

The strip holds 20 (`RECENT_N`, `RECENT_COLS`, `RECENT_PX` at the top of the module) 4 across by 5 deep; the title
reads *Recent (20 of 26)* once there are more files, the strip scrolls when the window is too short, and the scrollbar
hides itself when everything fits. *Select all* therefore covers 18; use **More…** for a bigger sweep.
There is no undo inside the window, but the delete itself is reversible in Explorer's Recycle Bin.

`test_gallery_ui.py` drives the real window with a stubbed engine and a temp gallery (clicks arrive as synthetic events
carrying the same state bits Tk sends), asserts 49 selection, delete and older-control checks, and screenshots each
state: `python test_gallery_ui.py --shots <dir>`.

## How much do seeds actually vary? (`variety_probe.py`)

Seeds decide almost nothing once the prompt names the scene. Measured with `variety_probe.py`, eight seeds
(1000-1007), z-image turbo, 512 px, 4 steps, and three independent measures:

| condition | words | detail | composition | palette |
|---|---|---|---|---|
| "a red bicycle leaning against a weathered brick wall at golden sunset" | 12 | 40.79 | 23.70 | **0.23** |
| "a red bicycle" | 3 | 40.84 | 27.63 | **1.17** |

Dropping nine words changed the palette 5.1x and the layout 1.17x, while full-resolution detail did not move at all
(40.79 vs 40.84) - detail is dominated by texture noise in both cases and is useless for this question. The same
eight seeds under the long prompt are eight takes on one photograph; under three words the seed chooses the backdrop,
frame type and accessories.

Changing the checkpoint moves it further, same prompt and seeds, all recorded in `variety/`:

| condition | detail | composition | palette | s/image |
|---|---|---|---|---|
| z-image turbo, 4 steps, cfg 1.0 (`prompt-specificity/p01`) | 40.79 | 23.70 | 0.23 | 5.6 |
| flux1 schnell, 4 steps, cfg 1.0 (`model-flux-schnell/p01`) | 57.90 | 41.68 | 0.51 | 14.2 |
| flux1 dev, 20 steps, cfg 3.5 (`model-flux-dev/p01`) | 70.23 | 58.63 | 0.61 | 34.2 |

Flux dev gives about 2.7x the palette spread - less than the 5.1x that loosening the prompt buys - at 6x the time:
34.2 s per image against 5.6 s. An earlier version of this file quoted 1.3 s and 3.9 s per image for the flux runs;
that was a bug in the throwaway script, which timed only the last image of each set. The figures above come from
variety_probe.py, which times every image.

Each folder holds `result.json` (seeds, settings, all four statistics) and the contact sheet; the images themselves go
to `%TEMP%\variety-images` because they are large. Re-run with:

    python variety_probe.py --prompt "a red bicycle leaning against a weathered brick wall at golden sunset" \
                            --prompt "a red bicycle" --count 8 --out variety/prompt-specificity

## Can an instruction change the pose without changing the character? (`pose_probe.py`)

One standing character, one instruction ("now sitting on a wooden chair, same face, same green coat"), swept across four
checkpoints and four denoise values. Two things must both hold, so each cell is judged on both: a vision model
(deepseek-flash) is shown the original and the edit and asked whether the character is the same and whether the figure is
seated. `S` = same character, `P` = seated, `SP` = both.

| model | 0.40 | 0.55 | 0.70 | 0.85 | works | s/image |
|---|---|---|---|---|---|---|
| z-image turbo | S | S | . | **SP** | 1/4 | 8 |
| flux1 schnell | S | S | S | P | 0/4 | 13 |
| **flux2 klein** | S | S | **SP** | **SP** | **2/4** | **8** |
| flux1 dev | S | S | **SP** | **SP** | 2/4 | 33 |

The finding is a **threshold, not a ranking**: below ~0.70 every model keeps the character and leaves her standing - the
sheet shows chairs appearing behind a figure still on her feet, because the prompt's object arrives and the pose does not.
Above the threshold the pose changes and identity starts to go; the threshold sits at 0.85 for z-image, 0.70 for flux2
klein and flux1 dev, and for flux1 schnell no value in the sweep both seated her and kept her (0.85 seats her, with a
different face).

**Practical answer: flux2 klein at denoise 0.70** - the same verdict as flux1 dev at a quarter of the time (8 s against
33 s). Reproduction: `python pose_probe.py`. Caveats: one seed per cell, one instruction, one character, and a cloud judge
- a repeat with three seeds per cell is the next step before treating 0.70 as exact.

For this job properly, the right tool is an instruction-editing checkpoint (FLUX.1 Kontext [dev], Qwen-Image-Edit) or
ControlNet/OpenPose conditioning, none of which is installed here; Kontext quantised is ~6-12 GiB against a 12 GiB card.

## Motion: animating the SVGs rather than the camera

The image models here cannot make video. What we *can* do is animate the SVG documents we already generate,
which keeps every frame crisp instead of scaling a bitmap:

    python svg_motion.py assemble grounded_ref/flow-1.svg out/assemble 48   # boxes slide in, arrows draw themselves
    python svg_motion.py reveal   grounded_ref/flow-1.svg out/reveal   48   # pure stroke-dashoffset line-draw
    python svg_morph.py  a.svg b.svg out/morph 48                           # structural morph; needs matched structure

Frame SVGs are rasterised once each in headless Chromium and encoded with ffmpeg (WSL). Measured clips,
the marker/dashoffset gotcha, the morph correspondence problem, and the video-model sizing are in
[MOTION-2026-10-22.md](MOTION-2026-10-22.md).

## Licence

**FSL-1.1-MIT** — see `LICENSE.md`. The scripts, harness, prompts and result documents here are licensed under the
Functional Source License 1.1 with an MIT future licence; it converts to MIT after two years.

Model weights and the third-party tools this work measures or drives (llama.cpp, ComfyUI, KiCad, Ollama, and the
diffusion and language models themselves) are **not** covered and are **not** distributed here — they remain under
their own licences. Nothing in this repository redistributes a model.
