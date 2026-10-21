# Image-model instrument: protocol v1, declared 2026-10-20

The LLM track's discipline, moved to image generation. The point is **not** to see pretty pictures; it is to
answer three questions per model with numbers that would survive someone else's rerun:

1. **Does it fit and stay up?** peak VRAM against the 12,227 MiB card, OOM count, load time.
2. **How fast is one image, honestly?** first-image latency (cold) *and* steady-state median/p95, because a
   diffusion graph has a warm-up phase exactly like a language model's first request.
3. **Does it obey the prompt, and what does quantisation cost?** a **machine-checkable battery** - the image
   analogue of the lab's answer letters - plus a **same-seed, cross-quantisation delta**, which is the closest
   analogue of next-token agreement.

## Fixed inputs (frozen before any model runs)

- **`prompts.json`** in this folder: **18 prompts in 6 categories**, each carrying `checks` that a scorer can
  answer yes/no, so a score is a count and not an opinion:
  `text` (an exact string must appear), `count` (an exact number of objects), `spatial` (a relation must hold),
  `attribute` (a named object must have a named colour), `negation` (a stated absent thing must be absent),
  `scene` (control: a plain description with no check, for the qualitative gallery only).
- **Fixed parameters for every arm**: **1024x1024**, the model's recommended step count and CFG for the turbo
  variant (recorded per arm, never "auto"), **seeds {1234, 5678, 9012}**, one image per (prompt, seed) = **54
  images per arm**, no prompt enhancement, no hidden refiner.
- **Scoring**: each image is scored on its prompt's checks by a **vision model with a frozen rubric**, asked one
  question per check with a yes/no answer and no free-text credit. The scorer is **cloud** (DeepSeek V4.1
  vision) and its prompt text is pinned in this folder; a local scorer would be a second variable we cannot yet
  hold constant, and our own reading-fidelity result says a local model can invert a two-sided comparison.
  Every arm is scored by the same scorer, so the scorer's bias cancels between arms - and is stated, not hidden.

## Measurements per arm

| measure | how | why it is the right one |
|---|---|---|
| load time | seconds from process start to first prompt accepted | separates "slow model" from "slow first image" |
| first-image latency | wall seconds, cold graph | the image equivalent of TTFT; the number a UI user feels |
| steady-state latency | median and p95 over 54 images, **first excluded** | matches how we report decode rate |
| peak VRAM | `nvidia-smi` sampled every 250 ms across the whole run | the q2c lesson: a silent clamp looks like a success |
| OOM / failure count | any exception, retried at most once with the failure recorded | failures are data, never quietly excluded |
| **checks passed** | out of 54 images x their checks | the correctness guard |
| **determinism** | the same (prompt, seed) run twice, PNG bytes compared | diffusion should be bit-reproducible at fixed seed; if not, that bounds every small delta |
| **quant delta** | same seed, two quants of one model, PSNR and SSIM computed with numpy | our next-token-agreement idea in pixels: sensitive to knobs that leave the checks unchanged |

## Gates, unchanged from the LLM track

Free physical **>= 9 GiB**, commit headroom **>= 8 GiB**, **GPU free >= 0.5 GiB** before a load, one workload at
a time, nothing killed to make room, no gate lowered. VRAM peak above the floor is a **finding**, not a pass.

## The arms, with their real download sizes (verified at source, 2026-10-20)

Sizes are the HF API's own numbers, not the blog post's:

| arm | components | download | notes |
|---|---|---|---|
| **SDXL-Turbo** (baseline) | 1 checkpoint + fixed VAE/TE | ~7.0 GB | the classic 1-4 step baseline; the cheap control that says whether a "4 second" claim is even plausible on this box |
| **FLUX.2-klein-4B, GGUF** | `unsloth/FLUX.2-klein-4B-GGUF` Q4_K_M **2.60 GB** (Q8_0 4.30, BF16 7.75) + `qwen_3_4b_fp4_flux2` **3.85 GB** + `flux2-vae` **0.34 GB** | **~6.8 GB** | Apache-2.0 on the 4B. The cheapest real arm and the first one to run |
| **Z-Image-Turbo** | `Comfy-Org/z_image_turbo` diffusion `int8_convrot` **6.20 GB** (nvfp4 4.51, bf16 12.31) + `qwen_3_4b_fp8_mixed` **5.63 GB** (fp4 3.48) + `ae` **0.34 GB** | **~8.3-12.2 GB** | Apache-2.0. int8 + fp4 encoder is the fit that leaves headroom; int8 + fp8 is ~12.2 GB and will need the floor checked |
| **FLUX.2-klein-9B, GGUF** | `unsloth/FLUX.2-klein-9B-GGUF` + the same family encoder | **TBD** | licence is **`other`** (not Apache) - check terms before downloading; runs last |

One economy worth having: **the text encoder family is shared** (`qwen_3_4b`), so one encoder download serves
both FLUX.2 and Z-Image arms.

**A correction to the brief that prompted this:** "Z-Image Turbo (6B)" is 6B only in the diffusion module's
name; the packaged Comfy bundle ships a **separate 4B Qwen3 text encoder** (8.04 GB bf16 / 5.63 fp8 / 3.48 fp4)
and a VAE, so "around 11.8 GB FP8" is the *whole stack resident at once*, which is why the cheaper quant
combinations matter on a 12 GB card. Likewise **FLUX Klein is `FLUX.2-klein`**, and there is a newer
**`flux-3-image-klein`** published 2026-10-14, which is not in the brief and is a separate decision.


## Harness readiness, verified 2026-10-20

- **ComfyUI Desktop 0.8.27**, basePath `C:\Users\kevin\Documents\ComfyUI`, `models/` holds only
  `v1-5-pruned-emaonly-fp16.safetensors`, `custom_nodes/` empty, `output/` empty.
- **Interpreter: `.venv\Scripts\python.exe` -> Python 3.12.11, torch 2.10.0+cu130, CUDA available,
  `NVIDIA GeForce RTX 5070`.** So the backend is ready; only weights are missing.
- **Invocation quirk that will waste an hour if missed:** Git Bash reports `command not found` for that
  interpreter even though the file exists and is executable. Run it through `cmd.exe`:
  `MSYS_NO_PATHCONV=1 cmd.exe /c ".venv\Scripts\python.exe path\to\script.py"`.
- Disk for weights: **C: 90.4 GB free, D: 252.8 GB free** (put model weights on D: and point ComfyUI's
  `extra_models_config.yaml` at them rather than filling the OS drive).
- Driver 610.60, card reporting **12,227 MiB**.

## Order of work

1. **Baseline first**: SDXL-Turbo, to calibrate "what a fast image on this box costs" and to prove the harness,
   the scorer and the determinism check end to end on a model nobody will argue about.
2. **FLUX.2-klein-4B Q4_K_M** (cheapest modern arm), with **Q8_0 as its quant pair** for the delta.
3. **Z-Image-Turbo** at int8 + fp4 encoder; if the floor is tight, record the peak and the clamp rather than
   lowering the gate.
4. **FLUX.2-klein-9B** only after its licence is read.

Every arm gets a prediction written **before** its run, in the style of the LLM queues: expected checks passed,
expected latency band, and the specific thing that would make me distrust the number.
