# FLUX.1-dev: blocked on this stack, not measured - 2026-10-20

The briefs' claim rests on **FLUX.1-dev at 20-30 steps**, so I tried to test exactly that. Every configuration
produced a **blurred image** whose high-frequency content is three orders of magnitude below a working arm's.
This is recorded as a **blocked arm**, not as a result: dev is neither vindicated nor refuted.

## What was tried, and what came out

Sharpness is Laplacian variance on the same prompt (`flow-1`), measured with `sharp.py`, against arms that work:

| configuration | wall | Laplacian variance | verdict |
|---|---|---|---|
| **klein-4B Q4_K_M** (working) | 5.0 s | **731** | reference |
| **Z-Image nvfp4** (working) | 5.1 s | **834** | reference |
| **schnell Q4_K_S, 4 steps** (working) | 10.1 s | **299** | reference |
| hand-drawn control | - | **1,200** | upper bound |
| dev Q4_K_S, 20 steps, FluxGuidance 3.5, cfg 1.0 | 47.7 s | **1.5** | blurred |
| dev Q4_K_S, 20 steps, same, **without** `--reserve-vram` | 46.7 s | **1.5** | blurred (identical) |
| dev Q4_K_S, **30 steps** | 55.8 s | **5.4** | blurred, slightly better |
| dev **Q5_K_S**, 20 steps, FluxGuidance 3.5 | 47.7 s | **1.1** | blurred |
| dev Q5_K_S, 20 steps, **classic CFG 3.5** (no FluxGuidance node) | 82.0 s | **0.6** | worst |

## What each variant rules out

- **Not the VRAM reservation.** With and without `--reserve-vram 1.2` the Laplacian is **1.5** in both runs, so the
  offload path is not the cause.
- **Not the quantisation.** Q4_K_S and Q5_K_S (8.29 GB) blur equally; schnell at Q4_K_S through the *same*
  `UnetLoaderGGUF` is crisp, so the loader and GGUF path work.
- **Not the guidance mechanism.** FluxGuidance 3.5 + cfg 1.0 and classic CFG 3.5 without the node both blur;
  the second is worse.
- **Not the steps.** 30 steps improves 1.5 -> 5.4 and is still two orders below a working arm.
- **Not the VAE.** All arms share `flux1-ae.safetensors` (verified at the official 335,304,388 bytes), and schnell
  renders sharply through it.

So the failure is specific to **dev weights through this stack**: ComfyUI 0.18.2 + `city96/ComfyUI-GGUF` on a
Blackwell card. The next things worth trying, none free: ComfyUI's own fp8 dev checkpoint (16.06 GB, which will
offload heavily on a 12 GB card), or the diffusers `FluxPipeline` path in Python, which bypasses the GGUF loader
entirely and is the cleaner way to answer the original question.

## Why I did not simply report "dev fails"

Because a blurred image is a **broken pipeline**, not a model property, and this lab's own rules say a run that
cannot be scored is reported as **unscored, never as a zero**. Writing "FLUX.1-dev is worse than klein" from these
five runs would be exactly the kind of claim the rest of this file exists to prevent.


## Follow-up: the blur is resolution-dependent, and 512x512 is sharp

Three more configurations, run after the first write-up, because "blocked" is only useful if it is precise:

| configuration | resolution | Laplacian variance |
|---|---|---|
| dev **fp8 checkpoint** (`CheckpointLoaderSimple`, no GGUF at all) | 1024 | **1.1** |
| dev fp8 checkpoint | **512** | **236.6** |
| dev Q4 GGUF, dual CLIP loader with **`device: cpu`** (T5 kept out of VRAM) | 1024 | **1.5** |

**What that rules out and what it leaves:**

- **Not the GGUF loader.** Comfy's own fp8 checkpoint, loaded by `CheckpointLoaderSimple`, blurs identically.
- **Not the text encoder's VRAM footprint.** Moving T5 to CPU changes nothing (1.5).
- **Not the model family.** The same weights at **512x512 render sharply (236.6)**, with 12 s instead of 40-82 s.
- **What is left is a resolution-dependent failure at 1024x1024 in this stack** - ComfyUI 0.18.2, fp8/GGUF FLUX
  dev, Blackwell, 12 GB - that silently produces a blurred image rather than an error. Schnell at the same
  1024x1024 through the same loader and VAE is sharp (299), so the family is not the variable; the
  dev weights at full resolution are.

**This is the same class of defect as the Strata prefill-chunk bug** that turned prompts above ~512 tokens into
unrelated text (#2856): a silent quality collapse that a successful exit status hides. It is worth recording for
that reason alone - **"the job succeeded" is not evidence the image is right**, which is why every arm in this
track is scored by an OCR and a trace pass rather than by trusting the return code.

**The honest next step, declared but not run:** score **all four arms at 512x512**, where dev demonstrably
works, and compare there. That is a protocol variant - the corpus resolution becomes an axis rather than a
constant - and it is the only way to answer the original claim on hardware that cannot hold dev at 1 MP.

## What stands from today regardless

- **Z-Image-Turbo is the diffusion arm to keep**: OCR 0.811, 13/20 fully labelled, didactic 36/60, 5.1 s.
- **Its VRAM floor status is a distribution, not a number**: across runs the peak was 11,165-11,794 MiB, i.e.
  **433-1,062 MiB free** - sometimes over the 512 MiB floor, sometimes not. `--reserve-vram 1.2` did **not** cap it
  (11,210 MiB on a cold single run, 11,794 MiB in the batch), so the flag is not a fix and the honest statement is
  that this configuration is **marginal against the floor**, not compliant.
- **The label validator is the best-value change made today**: 68/70 -> 70/70 in source, all 19 documents fully
  labelled, one repair round, no new weights.
