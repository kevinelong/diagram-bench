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
| ~~`flux2 klein`, `flux2 klein uncensored`~~ | `flux-2-klein-4b-Q4_K_M.gguf` (diffusion_models), `flux2-vae.safetensors` (vae) | 2.6 + 0.31 GiB | **retired from the picker 2026-10-28**; weights still on disk, every 4B measurement still in `RESULTS-*.md` |
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

**`ALL` has run live** (2026-10-28, same prompt, seed 1234, 1024 px): z-image turbo at 9 steps took **10.6 s** and
peaked at **7,455 MiB**; klein 9B at 4 steps took **24.5 s** and peaked at **9,749 MiB**, leaving **2,478 MiB** of the
12,227 MiB card - comfortably clear of the 512 MiB floor, so the 9B at 1024 px is now a measurement rather than a risk.
Both sidecars named the real profile and their own steps. On that prompt both wrote `OPEN` exactly, so the 9B's label
advantage from the 512 px diagram corpus did not reproduce here; the 9B's render was the more elaborate scene.

**The picker offers two models and an `ALL` option** (2026-10-28). `z-image turbo` and `flux2 klein 9b uncensored` are
the two; `ALL` renders one image from each at that profile's ceiling steps and the largest size the app offers (1024 px),
sequentially, cheapest first - so one press gives the same prompt across both models for comparison, and each image lands
in the gallery with its own sidecar. The step and size boxes are disabled while `ALL` is selected rather than ignored.
The 9B's peak at 1024 px is **unmeasured** (its measured point is 8,837 MiB at 512, from the 2026-10-25 three-arm run),
and the banner says so instead of inventing a number.

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
- **A reference can come from anywhere**, not just the output folder: **Paste image** accepts the three clipboard
  shapes above and refuses anything else with a sentence rather than an exception (a non-image file in a multi-file
  copy says "none of them is an image"; junk text says "nothing usable on the clipboard"). The size selector still
  decides what resolution the next run works at.
- **You know it is done when the status line changes.** The bar is determinate and the websocket reports
  `step x of 30` while the sampler runs, so it is not a spinner; at the end the status reads `clip done: <file>`, the
  hint gives frames, seconds, wall time, peak VRAM and where the file is, and the clip **opens by itself** in the
  default player. **Open clip** reopens the last one without rendering again.
- **The deliverable is an MP4**, not the WebP: ffmpeg on this box lives in WSL and its own WebP decoder rejects the
  animated file ComfyUI writes, so `video.py` decodes the frames with PIL, writes a PNG sequence and encodes H.264 from
  that. The WebP is kept beside it as the direct model output. The clip does **not** appear in the gallery, which lists
  PNG stills only.
- **Each container is opened by an app that understands it.** Windows associates `.mp4` with Media Player and `.webp`
  with Photos; handing the animated WebP to a media player is what produces *"the file is corrupt or unsupported"*, so an
  MP4 goes to the player and a WebP (a clip from before the MP4 step, or a failed encode) opens in the **browser**,
  which animates it. The hint says which happened ("no mp4 this time"). A failed encode now deletes its own output
  rather than leaving a 0-byte file with a `.mp4` name, and anything under 10 KiB is treated as a failure.
  Verified for the record: Windows Media Foundation reads our clip as `704x704, 2.04 s, FourCC H264, 7.8 Mbps`, the
  same FourCC as a known-good 1080p MP4 on this machine.
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

## Folders by prompt, without touching the disk

A gallery of 1,800 renders is a list of timestamps. **folders** (top right of the strip) derives the scene groups from
the prompt text in the sidecars - no image is decoded, no model runs, nothing is written - and shows them as chips:
`all`, then the biggest few, then **more…** for the rest. Selecting a chip filters the strip to that folder.

**What it uses, and what it deliberately does not.** Prompt text only. Two renders of the same scene at different seeds,
styles, strengths or quantisations share a prompt and belong together; two renders of different scenes do not, however
similar their pixels. The test that pins this writes one prompt onto three of the oldest pictures and asserts they land
in one folder.

**How the grouping was chosen, on evidence** (2026-10-27):
- plain token overlap **over-merges**: these prompts run to ~70 tokens of shared scaffolding, their nearest-neighbour
  similarity has a median of **0.96**, and at threshold 0.6 the 679 distinct prompts still collapse into 164 groups with
  a 144-image lump.
- **idf weighting** - down-weighting words that are common *in this corpus* - separates a labelled set of six scenes x
  six phrasings at **precision 1.00 / recall 1.00**; a rare-tokens-only version scores 0.00.
- **idf must come from the corpus, never from the pair being compared**: over two documents a shared token has df=2 and
  a unique one df=1, so sharing is penalised and a rephrased prompt scores ~0.20 against its own original. That bug is
  in the history; the module now fits a lexicon once and reuses it.
- **synonym-aware matching closes the last gap**: the rephrase button swaps words on purpose ("brickwork" -> "masonry"),
  so each token also contributes its dictionary synonyms at weight 0.45. With that, four rephrase turns keep every
  scene in one folder.

**What you see on load:** the newest 18 pictures stay **ungrouped and in date order** for immediate work, while the
grouping of everything older runs in the background (0.5-0.7 s for 1,810 pictures). **Four tiles that are really different.** A folder tile shows its newest four pictures that are visibly different from each
other: a 64-bit difference hash *and* a quadrant colour check, because a dHash alone cannot see that two pictures are
different colours (the test proved it - four plainly different colours hashed identically). The bar was raised from
8/64 bits and 24/255 colour to **16 and 40** after sheets were still four near-copies while clearly different pictures sat
further down: measured, the minimum pairwise difference inside a tile went from a median of **14 to 17 bits** for the same
cost (~7 ms per folder), and 7 more folders now honestly fall back to their newest four because they cannot produce four
different pictures at all.

**A stated head count is a subject.** "three very strong fit toned women" and "exactly six adult women sit
cross-legged" are different pictures, and text similarity cannot see it because the count is one word among many. The
count is read explicitly (`group_prompts.stated_count`, with the same rules the grader uses) and a cluster that holds two
different counts is split before a label is chosen; members that state no count join the largest part rather than being
exiled. Measured on the gallery: **2 of 85 folders mixed counts and between them held 363 pictures** - after the split,
**0 of 87**, intra-folder similarity 0.852 -> **0.882**, and the reported case is its own folder: *"Exactly six adult
women sit in a single shallow semicircle…"* (17 pictures) and *"exactly Six adult women sit cross-legged…"* (2).

**Folders are ordered by their
newest picture** - the one added to most recently comes first, which is what "last modified" means for a folder - with
the label as the tie-break so the order stays deterministic. A folder needs at least two pictures so a one-off is not
dressed up as a category, and a picture with no prompt is never folded - nothing can be said about it.

**Measured on the real gallery:** 1,810 pictures -> **56 folders**, 1,732 folded, 18 recent untouched, 60 ungrouped,
**0.5 s** of background work, folder sizes 303 / 196 / 154 / 142 / 100 / 68 / 64 / 56 / 53 / 42. Folder names are the
oldest member's opening sentence, elided - never invented, and occasionally a fragment, which is the honest cost of
naming a folder from a 70-token prompt.

## Folders: on by default, two per row, and a full-screen browser

`folders` is **checked at startup** and remembered; grouping runs once, in the background, just after the first paint.

Folder tiles are a **2x2 contact sheet of the folder's first four pictures**, so **two folders sit side by side** in the
first column, each with its full name wrapped in small type (never elided) and its picture count. A 3x3 sheet needed
~270 px and left room for exactly one folder per row - measured on a 1707x960 desktop, which is where the request came
from.

**Opening a folder does not replace the list.** Its pictures appear in a **second pane to the right of the folder
list**, **three across**, sized from the pane's live width and as tall as the column. Selecting in that pane never
moves the folder list. All three lists - pictures, folder tiles, folder contents - use **infinite scrolling**: they
show a first batch and append the next one when you reach the bottom.

**Three details that make the panes readable at a glance** (2026-10-27):

- **Folder titles are cut to ten words** (about three lines at tile width) with an ellipsis; the full sentence stays in
  the tile's tooltip. A 30-word prompt used to take six lines and push the next row of folders off the screen.
- **Every picture in the open-folder pane wears a size badge in its corner**: the file size (`2k`, `330k`, `1.9M`) in a
  real bitmap font (`Fixedsys`, because at 8 px an antialiased face is mush), coloured by band - **tomato red at 64 kB or
  less, amber yellow between, medium green at 1 MB or more**. The boundaries are inclusive at both ends, which a chain
  of `n <= limit` tests cannot express: exactly 1 MB is green and exactly 64 kB is red, and a test pins both. The badge
  takes the click that selects its picture rather than swallowing it.
- **The picture pane reports what the picture is**: `512x512 · 328k · seed 1007 · 4 steps`, in the tools row above it.

**The folder control is a button, not a dropdown.** It names what is open (`All folders`, `steampunk cyberdeck... (82)`)
and opens a **full-screen folder browser**: 4-up folder tiles on the left, that folder's pictures in a grid on the right,
both reflowing to the window and scrolling under the wheel. It is the same widget as View All (`ScrollGrid`), so there
is one interaction to learn and both scale the same way: **the column count comes from the live width and the tile grows
into the leftover space**, re-flowed on resize (debounced). Measured: a 1186 px pane shows six 190 px tiles rather than
eight 113 px ones with a gutter - the first version derived both the column count *and* the tile from the 96 px floor,
which converges and never grows.

**The wheel works over any of them.** On Windows Tk sends `<MouseWheel>` to the widget that has *focus*, not the one
under the pointer, so binding the canvases scrolled nothing and only dragging a scrollbar moved a list. One handler on
the toplevel now routes the wheel by what is under the cursor - checked from the deepest widget upwards, so a
thumbnail inside a pane still scrolls that pane - and returns `None` everywhere else, leaving the prompt box and any
dialog with their own scrolling. Measured with real events: strip `yview` 0.000 -> 0.020 on one notch, folder pane
0.000 -> 0.081.

`A/B` reviews the selected folder; the folder button opens the browser described above; the strip no longer has to
double as the folder index, so the treeview list that used to sit beside the selector is gone.

**This replaced a row of chips, because the chips broke the window.** Eight buttons carrying folder names asked for
about 1,330 px, and a grid column is always given at least the width its children request - so at 2560 px wide the
gallery column grew to 1,330 px and squeezed the prompt column and the picture pane to nothing (screenshot,
2026-10-27). Two further defects showed up in the same shot and are fixed and pinned by tests:

- the **strip and the whole button panel were gridded into the same cell** (`row=3` twice), so they drew over each
  other: buttons across the middle of the tiles, and a stray tile row peeking out under the hints;
- the strip's tiles were a **fixed three columns** whatever the width.

Now the order in the gallery column is title, search, folder button, buttons, strip, with the strip as the one flexible row,
so the buttons can never be pushed past the bottom edge; a resize re-flows the tile grid to the columns that actually
fit; and `test_gallery_ui.py` asserts that no two widgets share a grid cell in the gallery or prompt column, that the
buttons sit above the strip and inside the window, and that the selector offers every folder with names capped at 26
characters.

Measured at 1707x960 (the logical size of a 2560x1440 screen at 150 %): gallery 314 px, prompt column 547 px, picture
pane 846 px - the picture keeps the majority, which it did not before.

## Blender frames: the fixture that proves the animated path

`a4_fixture.py` (run inside Blender's own interpreter) writes **one SVG per frame** plus a single animatable SVG, and
`a4_check.py` scores the output: shared canvas, axis-aligned border, equal squares, a horizontal connector, **both
labels read back by RapidOCR on every frame**, and the moving square travelling exactly 69 px per frame with the START
label riding it. Two exporter facts are measured inside the fixture rather than assumed - it ignores the camera and
projects through a fixed **perspective** view (so the fixture fits a homography to nine probe strokes, residual
0.000 px) and it **crops to the drawing's bounds** (so the fixture draws a static border). Details and the three wrong
first answers are in [PIN-20261021.md](PIN-20261021.md).

## Platform icons

The icon masters are vendored in [`assets/platform-icons/`](assets/platform-icons/) - six editable SVGs (iOS, Android,
Windows; gradient and black/white), the Android adaptive layers, previews, and the vector reference. **Read
[`USAGE.md`](assets/platform-icons/USAGE.md) before any build or deployment**: it says which file belongs to which
context, the safe areas (Android's mark is 54x48 inside a 66x66 area on a 108 canvas; Windows is a 48 grid), and the part
that gets forgotten - iOS needs an Xcode asset catalog, Android needs VectorDrawables behind an `adaptive-icon` XML, and
Windows needs PNG/ICO with the thin rays simplified by hand for 16/24 px.

`previews/windows-gradient.png` is already the window icon, set with `iconphoto(default=True)` so dialogs inherit it.

## One prompt, several scenes

A prompt that marks two or more set-ups renders **one image per scene**. The split is `scenes.py`, which reads marker
lines only - `SCENE ONE - OPENING` headers, `1. INT. …` / `INT./EXT. …` slugs, `SHOT 3:` and `[scene 2]` - and treats a
`SCENE` header as the *title* of the set-ups beneath it, not as a picture of its own. Fewer than two markers is one
scene, so an ordinary prompt behaves exactly as before (`test_scenes.py` pins both halves).

Each render records `scene` / `scene_of` in its sidecar, the banner says how many images Generate will make, and the run
chains one scene at a time on the idle card. On the screenplay in `D:/Codex/House Rules 2026 v3/Pool Hall Misfits/`,
`scenes.detect` finds **86 set-ups** (the authored count) with a median of 254 words each.

**`order: Sequence`** reads a storyboard in film order: numbered pictures first, by the `scene` / `scene_of` their
sidecars record, then everything else newest-first - so a test render made today does not land in the middle of a scene.
The pane and the strip both honour it.

## What can start a render

Reported as "it starts to generate before I am expecting it". The full list, and each one's reason:

| trigger | why it exists |
|---|---|
| **Generate** | obvious |
| **Enter in the prompt box** | documented in the prompt's own label (*"Enter generates · Shift+Enter adds a line"*); Shift+Enter is bound first and inserts a newline |
| **Vary**, **Vary…** presets, **Upscale**, **Retry** | explicit buttons |
| **ALL** and **scene** runs | they continue on their own once started - that is what a sweep is |
| `--auto` | a launch flag, never a default |

**Changing a style, a strength, a size or a model never dispatches**: their traces only repaint the banner and the strength
readout (checked in the suite, because a control that quietly spends a minute of GPU is worse than a slow one).

**Double-clicking a picture used to generate** - on the strip and in the contents pane. Looking at a thumbnail twice in
quick succession fired a render with whatever prompt happened to be in the box, which is a browsing gesture doing the most
expensive thing in the app. That binding is gone; a double click in the View All browser still loads the picture into the
window, as it always did.

## A scene render is graded against its own scene

The multi-scene run already recorded which scene each picture was - now it also asks whether the picture *is* that scene.
When a scene image lands, a background thread runs `scene_grade.grade_scene(path, scene_text)` and writes `scene_check`
(`answer`, `reason`, `title`, `scorer`) into the picture's sidecar, then the window says which way it went: *"scene 2 of 6
graded: shows - <file>"*. A grader that cannot answer (no key, no service) reports **unavailable** with the reason and
never interrupts the render.

Only pictures whose sidecar carries a scene number are graded, so an ordinary render costs nothing extra.

## Asking the grader, instead of asserting

`scene_grade.py` puts a question to the same vision model `vlm_score.py` uses (one scorer, so its bias cancels between
comparisons) and does two jobs: grade a scene render against its own scene text, and gather measurements over the
gallery. Its first measurement, in [COUNT-FIDELITY-2026-10-28.md](COUNT-FIDELITY-2026-10-28.md), asks the plain
question - how many people are in this picture, against the number the prompt stated:

**11 of 14 scored exact (79 %), 3 real misses, 0 unreadable.** Small n, and it says nothing about unusual anatomy (the
gallery contains no "four arms" prompts at all).

An earlier version of this section claimed "10 of 10 exact, 4 abstentions". That was wrong, and the way it was wrong is
worth keeping: with a reasoning model, `finish_reason: length` returns **empty content** once its own reasoning has spent
the token budget. The instrument scored those as abstentions, so four lost answers looked like four honest refusals - and
the sample shrank to the pictures that happened to answer. `ask` now retries at 300 / 900 / 2400 tokens and reports
`no-answer` separately from `unclear`; the corrected numbers above are from the fixed instrument, at 0 lost.

Two bugs in the *instrument* were found by that measurement before it could report: a prompt reading "Pictured are six
women. Each clothed in three layers" was being scored as three, and contact sheets (16 tiles of two figures = 32) were
being scored as count failures. Both are fixed, and an ambiguous prompt (several different counts stated) now returns
"not measured" instead of guessing.

## What ALL does to the base and the strength

Measured rather than assumed: with a base armed and strength 0.62, **both** models in the sweep received `base_path` and
`denoise=0.62` - one image each at its own ceiling (turbo 9 steps, klein 4), the same edit parameters. The banner says so
now (`ALL · … · EDIT base.png at 0.62`), because it used to describe a plain text sweep while doing an edit.

ALL also can no longer dodge the style question: the sweep runs **after** the style/medium guard, so a prompt that names
"oil painting" while *cinematic* is selected asks Remove / Keep / Revise before the first model is dispatched - and
Revise starts no sweep at all.

**The strength readout is derived from the slider, not typed.** It was created with the literal string `0.60` and only
refreshed by a change trace, so at startup the label said 0.60 while Generate used the real value (0.70, the measured pose
threshold for klein). One number a user reads before pressing a button that spends a minute was wrong; the label is now
`'%.2f' % denoise.get()` at creation and on every change.

## A style and a medium in the prompt

If a style is selected and the prompt already names a medium, Generate asks before sending both. The words are
**highlighted in the prompt box** and a modal offers three ways out:

| choice | what happens |
|---|---|
| **Remove them and generate** | the ranges are cut, the leftover punctuation tidied, and the job is dispatched |
| **Keep and generate** | nothing changes and the job is dispatched |
| **Clear style and proceed** | the prompt keeps its own medium, the style suffix is dropped, and the job is dispatched - the mirror of *Remove*, which keeps the style and cuts the words |
| **Revise manually** | nothing is dispatched, the highlights stay lit, and the keyboard goes to the prompt |

Closing the dialog (or `WM_DELETE_WINDOW`) means **Revise**: losing a dialog never spends a minute of GPU.

Detection is deliberately narrow: it fires only when a style is set, only on whole words from a fixed list
(`oil painting`, `watercolour painting`, `painting`, `photograph`, `photo`, `film still`, `film`, `sketch`, `drawing`,
`illustration`, `anime`, `3d render`, `vector icon`), and **not** when the style's own suffix already says the word - a
photo style with "photograph" in the prompt is agreement, not a conflict. Measured on the fixture: `a lighthouse, oil
painting, stormy sea` with *cinematic* highlights `oil painting` only.

## The first column: what it is, and how it is set up

The column opens **folded**: the first line says what the list *is* (title, count, the folders switch) and a `▸ options`
button on that line reveals everything else - the search field, View all pictures, Folders, Session log, Open pictures
folder, the sort row and the selection hints. Measured, folding returns **~190 px** to the strip (872 -> 680 px at this
window size), which is then spent on the controls themselves: one `Big.TButton` style at 10 pt with real padding, and
hints written as sentences - *"Ctrl+click adds to the selection · Shift+click selects a range / Delete sends pictures to the
Recycle Bin · Ctrl+A selects all"* - instead of `ctrl+click adds · shift ranges · Del · Ctrl+A`.

The fold is a *visibility* group rather than a new parent: the widgets are the same ones the tests and the keyboard already
use, so nothing about their behaviour changes when the panel is closed.

## Prompting a figure into construction forms (mannequinization) - measured

*Mannequinization, construction, structural breakdown, form blocking* name a **process**. The models have a 77-token
encoder and no drawing pedagogy in it. Measured on one seated figure, both models, strength 0.72, seed 4242, one question
per artefact (`CONSTRUCTION-PROMPTS-2026-10-28.md` has the whole table):

| prompt shape | reads as blocks | reads as a drawing |
|---|---|---|
| *mannequinization: structural breakdown and form blocking into simple volumes* | **0 / 2** | 0 / 2 |
| *a figure reduced to simple 3D blocks ... soft grey clay* | 1 / 2 | 0 / 2 |
| *thin construction lines over the figure: centre line, box around the ribcage, cylinders on the limbs* | 0 / 2 | **2 / 2** |
| *an articulated wooden artist's mannequin in the same pose: boxy ribcage, cylindrical limbs, sphere joints, unshaded* | **2 / 2** | 0 / 2 |

So the two starters in the app are the two winners: `mannequin` (blocks, 0.72-0.80) and `construction` (a line drawing
with construction marks, 0.72, works in one pass on both models). For guides drawn *on* the blocks - the artist's planned
figure - feed the mannequin back as the base for `construction` at **0.55**: 3 of 4 measured second passes worked, and the
clean one is klein at 0.55 (centre lines, head circle, chest grid, a cross on the hip joint, poses intact). Turbo only
draws at 0.65 and there it prefers anatomy to geometry - it grew a literal ribcage and dropped the pose. Two findings worth keeping:

- **Pedagogy half-lands.** Turbo's answer to the art-school prompt was a *plaster mannequin still wearing the dress*: the
  word's morpheme fetched a mannequin object, while "breakdown into simple volumes" did nothing. A noun can be fetched; a
  decomposition cannot.
- **The question decides the winner.** The blocks question scores the best construction *drawing* as a failure, because a
  planning underdrawing keeps anatomy. Ask the question that matches the artefact wanted.

Wooden is more robust than "soft clay" (the clay lost on turbo to a sculpted ribcage), and in-prompt negatives bought
nothing - put exclusions in the negative field. Reproduce with `python tools/construction_trial.py`.

## Styles, and the strength a base image gets

Styles are prompt **suffixes** and carry the medium only - the subject stays in the prompt - so a style can never quietly
re-subject a picture. Three drawing media were added: `charcoal`, `pencil`, `blue pencil`, each inside 8 words so the
suffix does not eat the ~50-word encoder budget.

Every style now proposes a strength when a base image is used, and the number comes from measurement
(`STYLE-STRENGTH-2026-10-28.md`): on one photograph, 0.55 and 0.65 still render "a realistic photograph", and **0.72 is
where a medium change actually happens** - charcoal on textured paper, a blue-pencil underdrawing, the figure and room
intact. The genre styles (`icon`, `diagram`, `blueprint`) sit at 0.78, because a suffix cannot change genre: `diagram` at
0.78 produced "an illustrated portrait scene with labels", which is why the genre words live in the prompt (the
`construction` starter) instead.

The strength is also **model-specific**: turbo reads charcoal as "an oil painting" at 0.65 and 0.72 and only draws charcoal
at **0.78**, so it carries a measured **+0.06** (`MODEL_STRENGTH_OFFSET`); sweeping `ALL` takes the larger number. A style
never overwrites a strength you set by hand, and switching model re-proposes the number for that model only while the
slider still holds what we put there.

## The picture column

The picture is **pinned to the top right** of its column, with a small margin, so it is always in the same place instead of
floating in the middle and shifting as sizes change. Directly under it, right-aligned, is the **metadata block** - always
there when a picture is selected, two lines:

```
1024x1024  ·  1.6M  ·  seed 1236  ·  4 steps
flux2 klein 9b uncensored  ·  2026-10-28 13:25
```

The facts first, then where they came from (model, date, and the scene number when a picture came from a multi-scene run).
At the bottom of the same column sit **Make clip** and **Open clip** - in that order, which is the order the work happens
in, next to the picture they act on rather than in the prompt column.

## What a "vary" strength actually does

`vary_curve.py` measures it two ways, and the two disagree in an instructive way. The **population** curve - every
parent/child pair the gallery already holds - is not monotone band to band, because each band mixes bases, prompts and
models. The **controlled** sweep (`sweep_vary.py`: one base, one prompt, one seed, only the strength varying) is monotone
end to end, and it also shows that strength is quantised:

| denoise | grey difference | vs an unrelated pair | identical to |
|---|---|---|---|
| 0.40 | 0.0543 | 0.36x | |
| 0.55 | 0.0779 | 0.51x | |
| 0.65 | 0.1142 | 0.83x | |
| 0.72 | 0.1514 | 0.99x | |
| 0.78 / 0.80 | 0.1839 | 1.33x | each other |
| 0.84 / 0.87 / 0.90 | 0.2159 | 1.56x | each other |
| 0.95 | 0.2280 | 1.65x | |

Ten renders at ten strengths gave **seven distinct pictures**. Two consequences are now built in: the Vary menu offers
the measured distinct levels (0.40, 0.55, 0.72, 0.87, 0.95) instead of 0.35/0.60/0.80, and the report says plainly that
the sidecar records the strength asked for rather than the one that ran. Identity is a *pixel* hash - the PNG bytes
differ even for identical pictures, because ComfyUI embeds the workflow in each file. See
[VARY-STRENGTH-2026-10-28.md](VARY-STRENGTH-2026-10-28.md).

## What the existing gallery still says

`retrospect.py` reads the gallery and its sidecars and reports everything the files on disk can still answer - integrity,
history, the prompt corpus, the lineage, the folders scored retrospectively, and the blind spots. Its output is
[RETROSPECT-2026-10-28.md](RETROSPECT-2026-10-28.md). Nothing is written to the gallery and no future field is assumed:

```sh
python retrospect.py --gallery C:/Users/kevin/Pictures/zimage-ui --out RETROSPECT-<date>.md
```

Headline: **699 of 1,905 renders (37 %) used steps above the model's distilled ceiling**; **55 % of the 421 base renders
sit below denoise 0.75, where a "variation" is still closer to its parent than two unrelated pictures are**; 1,898
prompts collapse to **238** distinct ones at 0.90 cosine; and **1,899 of 1,905** pictures can be re-run exactly from their
own sidecar. What cannot be answered at all: sampler (never recorded), wins, rejections, per-render wall time and VRAM
(clips only).

## The toolkit question, parked

Whether Tk itself is the cause of the gallery's UI friction, and whether a native or browser-based shell would behave
more like Windows expects, is written up with the incidents classified and the options costed in
[UI-TOOLKIT-QUESTION-2026-10-28.md](UI-TOOLKIT-QUESTION-2026-10-28.md). Short version: about half the incident *classes*
are the toolkit's plumbing, all of the bugs we actually hit were ours, and a Rust rewrite would not have caught them - it
is the UI layer that decides the behaviour, not the language.

## The window takes the host's size and colours

The window opens **maximised**, confirmed after it is mapped (`state('zoomed')` is silently ignored on some Tk/Windows
combinations, and a later `minsize` can un-maximise it), and the process is **per-monitor DPI aware before Tk starts**
(`SetProcessDpiAwareness(2)`), because otherwise Windows scales the finished window as a bitmap and text goes soft on
every display that is not at 100 %.

Colours come from Windows, not from this file (`Theme`, `zimage_ui.py`), in three steps of authority:

- **Tk's system colour names** - `SystemWindow`, `SystemButtonFace`, `SystemWindowText`, `SystemGrayText`,
  `SystemHighlight`: on Windows these are `GetSysColor`, so they follow the user's classic colour settings and
  high-contrast themes;
- **`AppsUseLightTheme`** (`HKCU\...\Themes\Personalize`): dark mode is a *mode*, reported nowhere else, and without it a
  Tk app is a light island on a dark desktop. In dark mode the neutrals come from the mode (the classic system colours
  stay light), ttk switches to `clam` so its colours can actually be set, the title bar gets
  `DWMWA_USE_IMMERSIVE_DARK_MODE` through DWM, and the readonly-combobox and dropdown-list state maps are set - a
  readonly combobox draws its field from the *state map*, which is why the first dark pass still showed white boxes;
- **the DWM accent** (`HKCU\...\DWM\AccentColor`, stored AABBGGRR) for the selection ring, the status colour and the A/B
  overlay. This desktop's accent is olive `#758500`, and the app drew it rather than a blue of its own.

**The title bar follows Windows too**, for every window this app opens (View All, the folder browser, A/B review, the
log and metadata windows) and not just the main one: `DWMWA_USE_IMMERSIVE_DARK_MODE` for the mode, and
`DWMWA_CAPTION_COLOR` / `DWMWA_BORDER_COLOR` / `DWMWA_TEXT_COLOR` when "Show accent colour on title bars and window
borders" is on. That setting is `HKCU\...\DWM\ColorPrevalence`, i.e. `1`; on this desktop it is `0`, so no accent is
painted on the caption - the app follows the user's choice instead of forcing one. Caption text is black or white by
luminance, since an accent caption with the wrong text colour is worse than no accent.

Everything Windows does not provide - the backdrop behind a contact sheet, a hover tone, a field fill - is **derived**
from the window colour by mixing, so it tracks light/dark and contrast themes instead of being a second palette that
drifts. `test_gallery_ui.py` asserts the palette is real hex, that the prompt box and picture pane wear it, and that a
derived colour lies between the two it blends.

**What else Windows-hosted apps are expected to do**, from Microsoft's own guidance
([best practices](https://learn.microsoft.com/en-us/windows/apps/get-started/best-practices),
[theming](https://learn.microsoft.com/en-us/windows/apps/develop/ui/theming),
[dark and light for Win32](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-windows-themes),
[contrast themes](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/high-contrast-themes)) - and
where this app stands:

| expectation | here |
|---|---|
| per-monitor DPI awareness, no blurry scaling | **done** (`SetProcessDpiAwareness(2)` before Tk) |
| follow light/dark and the accent colour | **done** (`Theme`) |
| honour contrast themes | **partly** - system colours are used for chrome; the photo panes stay photographic |
| system font, system icons | **done** for text (Segoe UI, which is what Tk's default resolves to on Windows) |
| native window behaviour: maximise, snap layouts, rounded corners | **done** - the window is a plain Tk toplevel, so DWM owns the frame and snap |
| keyboard access to everything, mnemonics | **partly** - the APG layout-grid keys in all three lists (strip, View All, folder browser), plus Delete/Ctrl+A/Esc; no mnemonics |
| native file and folder dialogs | **done** (`tkinter.filedialog`, Explorer) |
| a responsive layout down to small windows, always scrollable | **done** - every list scrolls, tiles reflow |
| visible state for long operations, cancel | **done** - progress bar, ticks, Stop |
| window position and size remembered | **deliberately not** - it opens maximised, which is what was asked |
| taskbar progress, thumbnails, jump lists | **not done** - Windows-specific shell integration, no Tk path without a COM shim |

## Folders: why one subject had two folders, and the live view

**The defect, measured.** Two folders held variations of "black medium size standard poodle": 3 pictures without a style
prefix and 5 with "artists watercolor rendering of". The cause was not the style axis - clustering compared each prompt
only against its folder's **first (oldest) member**, and the oldest poodle prompt is the shortest one; the styled ones
score 0.01-0.09 against it and opened a second folder. Evidence that this was an artefact: the strongest **cross**-folder
pair measured **0.87** while each folder's own mean was **0.83** and **0.80**.

**The fix, and the three that were rejected** (all measured on this gallery, 1,783 distinct prompts):

| rule | folders | biggest | mean intra | poodles |
|---|---|---|---|---|
| anchor-only (before) | 93 | 206 | 0.873 | 5 + 3 |
| match any member instead of the anchor | 34 | 322 | 0.822 | 8 |
| any-member merge pass, floor 0.70 | 73 | 342 | 0.864 | 8 |
| **core-to-core merge at 0.70 (adopted)** | **86** | **223** | **0.875** | **8** |

Replacing the anchor comparison with member matching over-merges the whole gallery (34 folders, a 322-picture blob);
doing it as a *post-hoc* merge on every member is almost as bad (73). Comparing each folder's **core** - its most
specific member, the same one the verification pass uses - merges only the 7 genuinely duplicated pairs, costs 0.0 s, and
leaves the mean intra-folder cosine *higher* than before. A plan over 1,783 prompts takes 2.4 s.

**The strip no longer paints pictures it is about to replace.** Folders are the default view, so the 18 thumbnails drawn
at startup were replaced a few hundred milliseconds later - the flash of content that vanishes. The first paint now shows
`Grouping prompts… · 1,827 pictures` and grouping starts on the next idle cycle instead of after a timer: measured right
after construction, the strip holds **1 child and 0 photo tiles**, and still 0 once the folders arrive.

**The judge of a folder is its medoid, not its longest prompt.** A cluster used to be judged by its member with the most
raw text, which is how 11 pictures from *Fight Club* came to judge a *Matrix* scene: they joined the same bucket at
0.21-0.25 cosine, their longest prompt became the core, that core evicted the scene's six briefs by 0.012 of coverage,
and the scene split into two folders 0.188 apart. The core is now the member closest to all the others. Measured over
the gallery: 89 -> 87 folders, mean intra-folder cosine 0.859 -> 0.849, leave-one-out 98.78 -> 98.90 %, the scene is one
folder of 14, the 11 fight-club pictures are separated, and the flow-chart pair and poodle family are unchanged; the two
*style* variants of the cyberdeck merge into one folder of 47. A fixed cosine floor was tried first and rejected: it
cost the module's rephrase-recall fixture (1.00 -> 0.90), because a genuine rephrasing and a different scene occupy the
same 0.2-0.3 band.

**A name that is the opening of another name is the same subject.** Measured on the gallery: "flow chart" (2 pictures)
sat in its own folder beside "flow chart with start, processing and finish nodes" (24) - core-to-core **0.67**, just
under the 0.70 merge floor, although one name is literally the other with less detail. The same shape appeared for
"hacker cyberdeck screen". So the merge pass now also fires when one folder's core prompt **is a word-prefix** of
another's (at least two words, so a single word cannot merge anything), and it deliberately does *not* lower the floor:
0.65 would also have merged "steampunk cyberdeck" with "cyberdeck portable computer ... waterproof case" (0.69 by
cosine, different phrasing). Gallery effect: 91 -> **88 folders**, biggest 217 -> 205, mean intra 0.871 -> 0.858;
`flow chart` becomes **one** folder of 26, the `hacker cyberdeck screen` pair merges (23 + 17), and the four genuinely
different cyberdeck folders stay apart (10, 20, 37, 82).

**Folder tiles are slide mounts**: one 1 px frame holds the sheet, the picture count and the name together, with the
count between the pictures and the text and real spacing between tiles on both axes. The open one is drawn in reverse
video.

**The open folder is findable at a glance and is scrolled to.** It is drawn in **reverse video**: a 1 px line around
the contact sheet and between its four pictures (the sheet's own gutter in the same colour) with the caption on an
inverted bar. Opening a folder *automatically* - the one the prompt is predicted to join, or the one a selected picture
belongs to when folders are turned back on - scrolls it into view, and the strip's scrollregion is recomputed first,
because `yview_moveto` against the stale post-rebuild region silently clamped to the top.

**Captions that open a window end in an ellipsis**: `View All…`, `Run log…`, `A/B…`, `graph…`, `ⓘ metadata…`,
`Open dir…`, `Open clip…`, `Delete…`, `save style…`, and the folder button itself.

**The prompt is placed before it is rendered.** Pressing Generate predicts which folder the prompt would join and shows
it beside the folder button (`→ a lighthouse on a rocky…`); from the unfiltered *All folders* state it also opens that
folder, and a folder you are already reading is never replaced. Measured leave-one-out on the gallery - predict each of
1,756 member prompts against every folder without itself - the first guess is right **99.0 %** of the time, median
top1-top2 margin **0.42**, 99.5 % at a score of 0.85. The prediction runs on the *final* prompt (after any style suffix),
because that is the text that gets grouped.

**A recent picture is shown by exactly one folder - the one it would join.** The live view used to ask every folder
separately "would you accept this?" at the clustering's admission threshold, which is deliberately loose because
clustering follows it with a verification pass the live view never had. Measured: each of the newest 18 pictures appeared
in a mean of **4.4 folders**, one in **12 of 85**, and a render reported as miscategorised sat under "two adult women" at
cosine **0.208** - 0.008 over the bar - while its true home scored **1.000**. The question is singular, so the rule is
the argmax over all folders: that render now appears only in its own folder (1.000), and the folder it had been
mislabelled under no longer shows it.

**Closing during a render asks first, and stops it.** `Engine.shutdown` stops the worker and the server this app started,
but it does not interrupt the prompt - so attaching to a server you did not start (`--no-autostart`) and closing used to
leave the render running on the card. The close handler now counts the running job plus anything queued behind an ALL
sweep, asks, and on confirmation interrupts and clears the queue before shutting down.

**The caption follows the same theme as the window.** `winfo_id()` is Tk's *client* child (`TkChild`); the caption
belongs to its `TkTopLevel` parent, which does not exist until the window is mapped, and DWM rejects the child with
`E_HANDLE`. So the frame is resolved with `GetAncestor(winfo_id(), GA_ROOT)` at call time, and the request is repeated
on `<Map>` and once 250 ms after construction.

**The open folder's column names itself.** The header carries the folder's full title, wrapped, then the count
(`- 11 pictures`, plus `N new` for pictures the live view is offering), on the theme's text colour above the tile grid -
so a folder is identifiable from the pane itself, not only from a truncated label back in the first column.

**A folder is read newest-first.** The plan hands members over oldest-first so the folder order stays stable; the pane
re-sorts them newest-first, which is also where a fresh render lands.

**A render made while a folder is open appears in it *immediately*.** Two gaps were found and closed on 2026-10-28:
the pane is drawn by `_render_contents`, which only `select_folder` called, so a re-group that changed the plan left the
open folder's pane stale - the pictures only appeared after toggling folders or re-selecting the folder. It is now
re-rendered when a render lands (before the debounced re-group) and again whenever a plan is applied, so the plan and the
pane cannot disagree. Verified in the live window: pane 8 -> 9 with the new picture visible and no re-selection.

**A render made while a folder is open now appears in it.** Folders are derived from prompt text and the newest 18
pictures are deliberately outside every folder, so the folder you are standing in can never contain the picture you just
made. Two changes answer that: a finished render schedules a debounced **re-group** (1.2 s, on a worker), and the open
folder is rendered as a **live view** - its own members plus any picture outside every folder that the folder would
accept (`live_append`, same membership threshold), marked `new`. The re-group keeps the open folder by *name*, so the view
never closes underneath you. Verified on the real window with a synthetic render: pane 8 -> 9, the new tile marked.

## Video: why the 5B looked unusable, and the two ways out

**Make clip** and `video.py` ran Wan 2.2 **TI2V-5B** at GGUF **Q4_K_M**, 30 steps, cfg 5.0, shift 8.0, `uni_pc`/`simple`,
49 frames. Those settings are *not* the defect: they match the official `wan2_2_5B_ti2v` template, which asks for 20
steps at 121 frames. The ceilings were precision (4-bit on the smallest model), class (the 5B is the "fits 8 GB" tier)
and length (2 s clips have the least temporal context to keep an identity coherent).

**Two modes now, and they are two models, not a flag:**

| mode | model | files | grid | when |
|---|---|---|---|---|
| start-only | Wan 2.2 TI2V-5B | Q4_K_M 3.2 GB (Q6_K 4.21 / Q8_0 5.40 available) | 704², 1280x704 | one picture selected; fastest |
| **first -> last frame** | Wan 2.1 **I2V-14B 480p** | `wan2.1-i2v-14b-480p-Q3_K_M.gguf` 8.59 GB + `clip_vision_h` 1.26 GB + `wan_2.1_vae` 0.25 GB | 480², 832x480, 640x480 | **two pictures selected**; both endpoints are frames you drew |

The last one is why FLF was worth the download: `WanFirstLastFrameToVideo` is a *conditioning* node for the Wan 2.1 8x8x4
path - it feeds both images in as pixels, as CLIP-vision embeddings, and as the conditioning that pins where the clip
must arrive - so only the middle is invented and the drift that made diagram clips unusable cannot happen. It is not
available on the 5B, whose `Wan22ImageToVideoLatent` packs a start image into a 16x16x4 latent and has no end-image
input; asking for one there is refused before any weights load.

**A clip gets its own motion line.** An image prompt describes a still; an I2V model obeys motion language. The clip row
now carries an entry (`DEFAULT_MOTION`, remembered in settings) that is sent as the clip's prompt, so the prompt box can
keep describing the picture without telling the video to stand still.

**Measured, one clip, 2026-10-27** (Wan 2.1 I2V-14B 480p Q3_K_M, 480x480, 49 frames, 30 steps, cfg 5.0, shift 8.0,
`uni_pc`/`simple`): wall **480.0 s** for 2.04 s of 24 fps - **9.8 s per frame**, 235x real time, against the 5B's 2.2 s per
frame at 704 px. Peak VRAM **11,770 of 12,227 MiB = 457 MiB free**, under this lab's 512 MiB floor, so the 14B at 480p is
*at* the edge of the card, not comfortably inside it (`floor_breached: true` in the record; the model log reports
`loaded partially; 8213.12 MB usable, 8180.82 MB loaded, 138.61 MB offloaded`). Endpoint accuracy: frame 0 against the
supplied start **MAE 2.82**, last frame against the supplied end **13.50**, last against start **35.66** - the clip
arrives near the end frame rather than pinning it, and the motion is front-loaded (middle frame 19.89 from the start,
34.92 from the end). **65 %** of pixels changed by more than 12/255. One seed, one pair, no judged comparison: this is
"no failure observed", not an established quality verdict.

**Parameter levers that cost no download**, in order of expected gain: Q8_0 instead of Q4_K_M (5.40 GB), 121 frames
instead of 49 (5 s at 24 fps), 40 steps, `dpmpp_2m_sde`/`karras` or `res_multistep`/`beta` instead of
`uni_pc`/`simple`. `test_video.py` pins the wiring of both graphs and the four refusals that would otherwise cost a
failed run after the weights load.

## Ranking by wins: a non-destructive alternative to deleting

The A/B window opens in **Rank** mode: `Left wins (1)` / `Tie (2)` / `Right wins (3)` mark a winner and delete nothing.
`Delete` mode (the old `Delete left` / `Keep both` / `Delete right`) is one radio button away, and switching modes only
relabels the buttons - the same window, the same queue.

**A win belongs to the picture, not to the folder or the run.** The record lives in the picture's own `.json`, beside
its prompt:

| field | meaning |
|---|---|
| `wins` / `losses` / `ties` | bouts won, lost, tied |
| `bouts` | how many comparisons it has been in |
| `last_bout` | when it was last judged |

Because it sits on the file, a picture keeps its record when the gallery is regrouped, when it is moved to another
folder, and across restarts - and it **sorts first wherever it is shown next**, which is what "winners go first even
when moved to a different folder" needs. `order: Wins` in the gallery panel sorts the strip and the folder pane by
**most wins, then the better win rate, then fewer losses, then newest** (raw wins alone would let a picture that was
compared twenty times outrank one that won every bout it entered). The folder pane shows each picture's tally as a small
badge (`4W`, `4W 1L`).

Records accumulate; `Reset wins in this folder` in the window is the one explicit way to start over, and it clears only
the record, never the prompt or the file. Writing a sidecar is atomic (temp file + `os.replace`), so an interrupted
write cannot cost a picture its prompt.

## Clips: why they would not play, and what opens them now

The clips themselves were never broken. A `wan-clip-*.mp4` in the gallery is H.264 **High@3.1, 8-bit 4:2:0, 24 fps,
2.04 s, 49 frames**, written by our own `libx264 -pix_fmt yuv420p -movflags +faststart`; an independent decode returns
all 49 frames at 704x704, and VLC plays it and exits 0.

What was broken was the **handler**: `.mp4` and `.webm` both have a `UserChoice` pointing at the Windows Media Player
app package, and the `HKLM` fallback for `.mp4` is `WMP11.AssocFile.MP4`, a handler Windows 11 no longer ships. The
player reported `0xC00D36C4` ("unsupported, wrong extension, or corrupt") on files that decode perfectly.

Two changes, in this order of importance:

1. **The app opens clips in a player that exists.** `App.find_player()` picks VLC (installed here, 3.0.16) or mpv, and
   launches it detached and *visible* through `winproc.launch` - `winproc.popen` would start it with `SW_HIDE`, which is
   right for consoles and wrong for a player. `ZIMAGE_PLAYER` overrides. When no player is installed it falls back to
   the Windows association. `--play-and-exit` is deliberately not used: a two-second clip would close the window as it
   ended, so the window stays open with the clip loaded and its controls available.
2. **A codec note in the hint.** `video.mp4_codec()` reads the sample-entry fourcc, and a non-AVC clip says so
   (`hvc1`, `av01`) instead of leaving a bare failure - Windows needs the HEVC or AV1 extension for those, VLC does not.

`tools/set-clip-default.ps1` points `HKCU\Software\Classes\.mp4/.webm` at `VLC.mp4`/`VLC.webm` and backs up what it
replaced to `%LOCALAPPDATA%\zimage-ui\clip-default-backup-*.reg` (`-Undo` restores it). **It cannot finish the job on
its own and says so:** Windows hash-protects `UserChoice` and denies both `Remove-Item` and `reg delete`, so making
double-click work is two clicks in Settings > Apps > Default apps > VLC media player (the script opens that page).
The in-app player path needs none of it.

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
- **The sampler is load-bearing for text, and it generalises across profiles.** On klein 4B a single prompt went
  from **0.75** OCR exact with `euler`/`simple` to **0.25** with `dpmpp_2m`/`karras`; on z-image over the whole
  ten-prompt corpus it goes **0.736 -> 0.325**, full matches **5/10 -> 2/10**, and traced paths **32 -> 538** - the
  sampler that looks like a free quality knob draws noisier geometry for the same words. Measured 2026-10-26 at 512 px.
- **The negative prompt is inert on every profile measured, twice.** A real negative prompt returned a
  **byte-identical** image at cfg 1.0 on klein 4B *and* on z-image (mean pixel difference 0.00), because a distilled
  model at guidance 1.0 runs no unconditional pass. Guidance above 1 does move the image (5-6 % of pixels at cfg 2.0
  and 4.0) without changing a label.
- **For z-image, steps are a geometry knob, not a text knob.** 4 steps against 9 on the same corpus: OCR **0.732 vs
  0.736** and 5/10 full matches both, but traced paths **106 vs 32** - the same words drawn twice as messily. So the
  profile now runs **9 steps**, which is what the picker already applied and what the published row used; the "distilled
  for 4" line was inherited from the klein arms rather than measured on this model.
- **Two profile families were deleted, with their weights** - `flux1 schnell` and `flux1 dev` were dominated on time,
  VRAM and labels by the profiles that remain; deleting them freed **41 GiB**. The four that remain are z-image turbo
  and the three klein arms, and the klein pair is not a subset relationship: one is the control, the other the cheap
  unfiltered arm.
- **A quantisation we hold cannot be loaded.** `z_image_turbo_int8_convrot.safetensors` raises
  `KeyError: 'int8_tensorwise'` in this ComfyUI, whose loader registers only fp8-e4m3/e5m2, nvfp4 and mxfp8, so
  nvfp4-versus-convrot is **gated on a ComfyUI upgrade** rather than measured.

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
- **Anatomy has a first judged run, and it is coarse only.** `qwen3-vl:4b` is now installed locally (3.3 GiB) and
  judges on-box, so no figure study leaves the machine. It was calibrated before use: **10/10** on pose discrimination
  across four poses including two negative controls, **4/4** on nonsense controls (no bicycle, no car), and it read a
  render's `VAILDATE` as `VAIDDATE` - one letter off, which is why label scoring stays with deterministic OCR.
  The run (9B, 704 px, 4 poses) gives **coarse 19/19, fine 9/9**, and the *fine* tier - five fingers, toes, head-to-body
  proportion - is **not yet validated**: a judge that answers yes to every digit question at 704 px has not been shown
  to count. Broken out in the result file as `coarse_*` and `fine_*` rather than averaged into one number.
  Peak with judge and image model both resident: **11,725 MiB**, so `local_vlm.unload()` first (measured 1,314 MiB
  idle afterwards).
- **Label binding inside a diagram is unsolved**, and it is a content failure rather than a prompt-wording failure: a
  tree's leaves carry the prompt's own numbers instead of 1..7 at every density. A LoRA trained on our own corpus is
  the untried lever.
- **The 9B has now been pose-profed, and it did not move the threshold.** On the local judge, with the 2026-10-24 base
  image reused byte-identical, `flux2 klein 9b uncensored` works at denoise **0.70** and above - the same cells as the
  4B - at 3-5x the wall time and ~3 GiB more VRAM, and the face question could not separate the two models at all. The
  threshold reads as a property of the denoise rather than of the model. One seed per cell, 512 px only, no bracket
  between 0.55 and 0.70 ([POSE-LADDER-9B-2026-10-27.md](POSE-LADDER-9B-2026-10-27.md)).
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

The seed **steps on by one after every generation** (`next seed 4243 (+1)`), so pressing Enter twice gives two different
images and the sequence is reproducible from the first one - a random jump is a value you cannot write down in advance
and cannot walk back. **hold seed** next to the chain box pins it instead: ticked, a render leaves the seed exactly
where it is, for when the same seed is wanted again on purpose. It is deliberately **not remembered** between windows,
for the same reason the chain box is not. The **Rnd** button is still the way to ask for a random one. The banner shows
`seed 4242 (held)` when it is pinned.

The exception to the increment is an edit you armed with **Use as Base**: that picture's seed comes back with it and
stays for the run, so an edit is reproducible (`seed 555 kept`). A plain click in the gallery restores the seed so you
can read or reproduce the picture, but the next generation still increments, because looking at an image is not
editing it.

Controls: **steps** (2, 4, 6, 8, 9, 12, 20, 30, 50 - and **changing the model sets it to that model's ceiling**:
50 for `flux1 dev`, 9 for z-image, 4 for the distilled turbo models; above the ceiling costs time and changes nothing,
measured on 2026-10-20/21), **sampler** (euler or euler_ancestral - the latter injects noise
per step, which adds grain and is the real "randomisation" knob), **strength** for img2img, and **one at a time
(seed+1)**, which is the default: an N-up is N separate requests with seeds seed..seed+N-1, so every tile is
individually reproducible and the VRAM peak stays that of a single image. Unticking it asks ComfyUI for one batch,
about 8x faster (6 s vs 50 s for 9-up at 256 px) with a much higher peak.

**rephrase** sits directly under the prompt box and rewrites it with synonyms - no model, no server, so it always works.
Two boxes beside it decide what it does:

| control | values | what it means |
|---|---|---|
| **intensity** | `light` / `normal` / `strong` | how many matched words move on one press. `light` takes every third one, rotating so none is starved; `normal` takes all; `strong` takes all and prefers the farthest synonym in each list |
| **matching** | `off` / `strict` / `loose` | how forgiving the lookup is. `off` = the word itself or an inflected form of a dictionary word; `strict` also fixes a listed typo and accepts **one** edit on a word of six letters or more sharing two opening letters; `loose` accepts **two** edits and one shared letter |

A word is matched in four ways, in this order: **exact**, **stem** (a plural, past, gerund or adverb form: "columns"
finds "column", "softly" finds "soft" and comes back as "gently"; the replacement is re-inflected so "two columns"
becomes "two towers", not "two tower"), **spelling**
(en-GB/en-US pairs such as *colour/color*, *grey/gray*), then **fuzzy** - and a hyphenated compound is matched part by
part, so *silk-towel-covered* keeps its first two words and swaps the third. Listed typos are their own table
(`weathred` -> `weathered`) and apply even with matching off, because those are entries, not guesses.

**Adverbs only move when their adjective is in the dictionary**, which is why `softly` becomes `gently` but
`culturally` and `explicitly` do not: `cultural` and `explicit` are not visual vocabulary, and swapping them would
change the meaning of the prompt rather than its wording ("culturally diverse" is not "societally diverse"). Measured on
the 20 real prompts: 7 adverbs, **0** of them with a dictionary adjective behind them.

Fuzzy is deliberately tight. A loose similarity rule matched **"three" to "tree"** and **"steam" to "stem"**, silently
changing what the picture is about, so numbers and function words are excluded outright and the minimum word length is
six in both modes. A miss costs a word; a wrong word costs the image.

Every control carries a one-line tooltip, and a **banner** above the progress bar states what the next Generate will
do before it is pressed: `TEXT → IMAGE · 4 steps · euler · 512 px · one image · seed 1234`, switching to
`EDIT img.png at 0.60` when a base is set. Choosing a layout prints its measured cost (`16 tiles · ≈ 3 m 39 s one at a
time, or ≈ 11 s as one batch`), from a table of timings taken on this machine rather than a formula.

Window size, steps, sampler and layout are remembered in `%LOCALAPPDATA%/zimage-ui/settings.json`, and **Ctrl+Up /
Ctrl+Down** in the prompt box recalls previous prompts - plain Up/Down are left to the textbox, because a five-line prompt
needs the caret to move by line before any shortcut gets to hijack the key. The *chain* box is deliberately **not** remembered, so a fresh window always starts
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

**Guidance (CFG) is deliberately not exposed**, because it was measured and removed: on the turbo models raising it from
1.0 to 1.5 halves edge energy (3.91 -> 1.92) while OCR still reads the same 4/4 labels, so it buys no adherence and costs
crispness. CFG stays at the model's distilled 1.0.

**What that means for the negative prompt, measured 2026-10-26** (klein 4B, 512 px, seed 1234, frozen flow-1 prompt,
same weights, one variable at a time):

| change | mean pixel difference vs baseline | OCR exact | wall | peak |
|---|---|---|---|---|
| baseline (cfg 1.0, euler/simple, empty negative) | - | 0.75 | 7.2 s | 5,925 MiB |
| **real negative prompt** (`blurry, low quality, watermark, text`) | **0.00 - byte-identical** | 0.75 | 8.1 s | 5,743 |
| cfg 2.0 | 5.87 | 0.75 | 112.4 s (outlier) | 9,791 |
| cfg 4.0 | 8.16 | 0.75 | 8.1 s | 5,839 |
| **sampler `dpmpp_2m` / `karras`** | 14.90 | **0.25** | 8.2 s | 5,552 |

So **the negative box does nothing on these profiles**, and the box now says so: at cfg 1.0 there is no unconditional
pass, so there is nothing for a negative to steer. Guidance above 1 does change the picture (5-6 % of pixels) without
changing the labels, and the **sampler is not a free choice**: switching from `euler`/`simple` to `dpmpp_2m`/`karras`
left fewer than a third of the labels readable on the same seed. `euler`/`simple` stays the default for that reason,
not for taste.

**One quantisation experiment is blocked, and this is why:** `z_image_turbo_int8_convrot.safetensors` (6.20 GiB, on disk)
cannot be loaded by this ComfyUI build at all - `UNETLoader` raises `KeyError: 'int8_tensorwise'` because
`comfy/quant_ops.py` registers only `float8_e4m3fn`, `float8_e5m2`, `nvfp4` and (when available) `mxfp8`. The convrot
*kernels* are present in `comfy_kitchen`; the loader's format table is not. An nvfp4-vs-convrot comparison needs a
ComfyUI upgrade, which is a declared change affecting every image row, so it is deferred rather than fudged.

Size and layout are chosen in the toolbar: **256, 320, 384, 448, 512, 640, 704, 768, 1024**, and
**single / 4-up (2x2) / 9-up (3x3) / 16-up (4x4)**. **704 is there for video**: it is the clip model's 1:1 grid, so a
still rendered at 704x704 goes into `prepare_still` and comes out **pixel-identical** (measured: max channel difference
0), with no resample between what the image model drew and what the video model reads. Measured cost of that still:
**12.1 s** and **4,671 MiB** peak with `flux2 klein` - against 1024 px, which is the reference for the diagram metrics.
A 16:9 clip still resamples even from a 704 still: the window renders squares only, so filling 1280 wide means
upscaling whatever it is given.
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

**The defaults are the measured pose combo: flux2 klein at strength 0.70.** That pairing both seats the figure and keeps
her face from denoise 0.70 upward, at 8-13 s and 5.0-5.5 GiB - lighter than the previous default (z-image, 6.7 GiB) and
quicker to edit, and re-measured 2026-10-27 on the local judge with the 9B alongside it: the 9B works at the same
threshold for 3-5x the time and ~3 GiB more
([POSE-LADDER-9B-2026-10-27.md](POSE-LADDER-9B-2026-10-27.md)). The trade-off is honest: for pure text-to-image z-image
turbo is still the faster choice (5.6 s against ~8 s), so it is one click away in the model picker. Both are remembered
per install; strength is on the slider and persisted alongside the model.

| profile | weights | encoder | measured at 256 px, 4 steps |
|---|---|---|---|
| z-image turbo | `z_image_turbo_nvfp4` (UNETLoader) | qwen_3_4b (lumina2) | **7.4 s** |
| flux2 klein | `flux-2-klein-4b-Q4_K_M.gguf` (UnetLoaderGGUF) | qwen_3_4b_fp4_flux2 (flux2) | **9.8 s** at 512 px, 4 steps, cfg 1.0 |
| flux2 klein uncensored | the same `flux-2-klein-4b-Q4_K_M.gguf` | `flux2-klein-4b-uncensored-q4_k_m.gguf` (flux2, **CLIPLoaderGGUF**) | 7.1 s vs 13.3 s cold, 512 px, same seed |
| flux2 klein 9b uncensored | `flux-2-klein-9b-Q4_K_M.gguf` (UnetLoaderGGUF) | `flux2-klein-9b-uncensored-te-q5_k_m.gguf` (flux2, **CLIPLoaderGGUF**) | **32.5 s** median, 8.8 GiB peak |

**Two profiles were deleted on 2026-10-26, with their weights.** `flux1 schnell` and `flux1 dev` were dominated on
every axis measured at 512 px on the frozen corpus, one seed: schnell was slower than z-image (**6.1 s vs 2.0 s**), heavier
(**10,502 vs 9,269 MiB**) and worse on labels (**0.749 vs 0.786**); dev cost **11.2 s** with an **11,803 MiB** peak - a
breach of the 0.5 GiB floor - for **0.704**. Removing them freed **41 GiB** on `D:` (86 -> 44 GiB of weights): both
diffusion GGUFs, the unused dev Q5_K_S, the 16 GiB dev fp8 checkpoint, and the T5/clip_l/flux-VAE that only they used.
**The 2026-10-20/21 arms that cite them are no longer reproducible** and are kept as records; reinstating either is a
~12 GiB re-download and one `MODELS` entry. What remains: z-image turbo, flux2 klein, flux2 klein uncensored,
flux2 klein 9b uncensored - plus the Wan 2.2 video stack.

The two klein arms are not substitutes for each other: `flux2 klein` is the control that makes the uncensored row mean
something (same diffusion weights, shipped fp4 encoder), and `flux2 klein uncensored` is the cheap unfiltered arm at half
the 9B's time and VRAM.

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
**Crop to base** closes the loop the other features open: masking repaints inside one picture, expanding grows its
frame, and cropping lifts a region *out* to be the subject of the next pass. The crop is taken from the full-resolution
image behind the canvas, not from the scaled copy on screen, so a small framing does not start from a soft 260 px
version of itself; the size selector then decides what resolution the next run works at. The crop keeps the source's
prompt and seed in its sidecar (with `crop` and `source_image` added), so the chain is readable afterwards.

Editing honours the size selector: an img2img or inpaint request **resizes the base to the chosen size** (centre
crop, so the aspect is kept) instead of inheriting the base's dimensions. Measured: a 256 px base asked for 512
returned 512x512 in 7.0 s, asked for 384 returned 384x384 in 6.7 s, and an inpaint with a 256 base and 256 mask at
384 returned 384x384 in 8.0 s - the mask travels through the same resize (MaskToImage -> ImageScale -> ImageToMask)
so it still lines up.

| **hires x2** | real Hires. fix: render at the current size, then `LatentUpscaleBy` and a second pass at denoise 0.45 | **7.8 s** for 384 to 768 px |
| **paint mask -> Fill selection** | the brush lives on the picture: paint, and the Generate button becomes *Fill selection*; only painted pixels pass through `VAEEncodeForInpaint` | **8.9 s** on a 1040 px base |
| **expand** | the pad arrows grow one side, the button becomes *Expand* via `ImagePadForOutpaint` | **11.6 s**, 256 to 384 px |
| **pin / compare** | pin any picture, then drag the split against whatever is on screen - not only against the base | - |
| **Paste image** (or `Ctrl+V`) | brings a picture from outside the folder in as the base: a **bitmap** (screenshot, browser "copy image"), a **copied file** (Explorer), or **copied text that is a path** to an image. Written as `pasted-<stamp>.png` with a sidecar recording the origin, then armed as the base like any other | verified on the real Windows clipboard: a 512x512 bitmap pasted from Explorer/PowerShell became the base, sidecar `{'source': 'clipboard', 'width': 512, 'height': 512}` |
| **\u2702 crop** | arm it, drag a rectangle on the picture; that region is saved as a PNG, recorded in its sidecar with the source path and the box, and becomes the base - so the next Enter edits only what you framed. Mutually exclusive with painting a mask, because both start with a drag | measured 352x352 crop from a 704 px render used as a 512 px base: one 4-step klein run in **7.2 s** |


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

### Keyboard in the gallery

Three lists answer the keyboard the same way - the **Recent** strip (both modes: pictures and, with folders on, folder
tiles), the **View All** window, and the two columns of the prompt-folder browser. They follow the W3C APG *layout
grid* pattern (<https://www.w3.org/WAI/ARIA/apg/patterns/grid/>), which is why movement is horizontal-wrap and
vertical-clamp rather than a plain index step:

| key | effect |
|---|---|
| **left / right** | one cell; the right-hand end of a row wraps to the first cell of the next row, and the left-hand end to the last cell of the previous row |
| **up / down** | one cell in the same column; at the top or bottom it does not move, and a short last row is never entered sideways |
| **PgUp / PgDn** | one whole visible set of rows, keeping the column, then scrolled so the new row is among the first visible ones |
| **Home / End** | first / last cell of the *current row* |
| **Ctrl+Home / Ctrl+End** | first / last cell of the whole list |
| **Space** | toggles the current cell (opens a folder tile - a folder list has one selection, the open folder) |
| **Enter** | activates it: loads the picture, or opens the folder |
| **Shift+arrows** | extends the selection from the anchor, as shift+click does |

**Which list answers the keys is which list you clicked.** The open folder's contents pane is a list in its own right:
clicking a picture there gives it the keyboard and the arrows move *its* cursor (left/right wrap across its rows,
up/down keep the column, PgUp/PgDn move a screenful, Home/End the row, Ctrl+Home/End the pane), leaving the strip alone;
clicking a tile in column 0 hands the keyboard back. A re-render of the pane keeps the keyboard where it was, so a
re-group does not silently move your arrows to the other column.
| **Ctrl+A** | selects everything laid out |

The keys are bound to the list itself, never to the whole application, so typing in the prompt box or in the View All
search field is untouched. A click puts the cursor where the mouse is and hands the list the keyboard; movement scrolls
only when the cell under the cursor is actually out of view, so it never disturbs the rule that a resize does not move
the view.

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

One standing character, one instruction ("now sitting on a wooden chair, same face, same green coat"), swept across
denoise values. Two things must both hold, so each cell is judged on both: a vision model answers whether the character
is still there and whether the figure is seated. `S` = same character, `P` = seated, `SP` = both.

The judge is now **local** (`--judge local`, the default): `qwen3-vl:4b` answers three short questions - the
character's attributes still present, seated, standing - with the pose asked in both directions so a contradiction is
recorded rather than resolved, plus a two-panel face comparison kept apart as a separate signal. **No figure study
leaves the machine**; `--judge cloud` still reaches `deepseek-flash` for comparison with the 2026-10-24 run. The judge
is re-calibrated inside every run (`--controls`, **9/9** on 2026-10-27: standing base, generated seated reference,
generated different character), because inheriting a calibration is how an instrument drifts.

| model | 0.40 | 0.55 | 0.70 | 0.85 | works | s/image |
|---|---|---|---|---|---|---|
| **flux2 klein (4B)** | S | S | **SP** | **SP** | **2/4** | 8-13 |
| flux2 klein 9B uncensored | S | . | **SP** | **SP** | 2/4 | 23-39 |

**The 9B threshold is 0.70 - the same as the 4B's**, now measured with one judge instead of compared across two. Both
models leave her standing at 0.40 and 0.55 and seat her at 0.70, and the pixel distance at the crossing is the same
to within 0.8 (**32.63** for the 4B, **33.40** for the 9B), so the threshold reads as **a property of the denoise, not
of the model**: below it the prompt's object arrives - chairs appear behind a figure still on her feet - and above it
the pose follows. The 9B costs **3-5x the wall time and ~3 GiB more VRAM** for that same verdict, and the face-only
question answered "same face" for all four 0.70/0.85 renders of **both** models, so it bought **no measurable identity
headroom**. Peak with the judge and a model both resident: **11,890 MiB of 12,227**. The full table, the two
calibration errors that had to be fixed first, and the caveats in
[POSE-LADDER-9B-2026-10-27.md](POSE-LADDER-9B-2026-10-27.md); result in `pose-ladder-9b/result.json`.

**Practical answer: flux2 klein at denoise 0.70** - unchanged, and now a same-instrument result. Reproduction:
`python pose_probe.py --profiles "flux2 klein" "flux2 klein 9b uncensored" --out pose-ladder-9b`. Caveats: one seed per
cell, one instruction, one character, 512 px only, and no bracket between 0.55 and 0.70 - so 0.70 is the lowest *tested*
working value, not the located transition. The identity half of "works" is the weak half: read a working cell as "the
coat and hair survived and she is seated", not "the face is identical", because the face visibly drifts at 0.85 and the
judge does not call it a different person.

The 2026-10-24 cloud-judged sweep is kept below as the record it is; `flux1 dev` and `flux1 schnell` have since been
deleted from the models, so that table is no longer reproducible here.

| model (cloud judge) | 0.40 | 0.55 | 0.70 | 0.85 | works | s/image |
|---|---|---|---|---|---|---|
| z-image turbo | S | S | . | **SP** | 1/4 | 8 |
| flux1 schnell | S | S | S | P | 0/4 | 13 |
| **flux2 klein** | S | S | **SP** | **SP** | **2/4** | **8** |
| flux1 dev | S | S | **SP** | **SP** | 2/4 | 33 |

That run put the threshold at 0.85 for z-image, 0.70 for flux2 klein and flux1 dev, and found no value for flux1
schnell that both seated her and kept her.

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
