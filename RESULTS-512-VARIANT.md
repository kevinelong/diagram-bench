# The 512x512 variant: dev works there, and the ranking does not change - 2026-10-20

FLUX.1-dev blurs at 1024 on this card but renders sharply at 512, so all four arms were re-run at **512x512**
to answer the original claim on hardware that can hold dev. Ten prompts, one seed (1234), 10 images per arm.

## Diffusion arms at 512 (like-for-like)

| arm | wall, median | peak VRAM | paths | axis | colours | OCR exact | fully labelled |
|---|---|---|---|---|---|---|---|
| **Z-Image-Turbo nvfp4**, 9 steps | **2.0 s** | **9,269 (2,958 free)** | **31.5** | 100 % | 2 | **0.786** | **6/10** |
| FLUX.1-schnell Q4_K_S, 4 steps | 6.1 s | 10,502 (1,725 free) | 62.5 | 100 % | 1 | 0.749 | 5/10 |
| **FLUX.1-dev fp8**, 20 steps | 11.2 s | 11,803 (424 free) **floor breach** | 105.5 | 95.3 % | 4 | 0.704 | 5/10 |
| klein-4B Q4_K_M, 4 steps | 3.0 s | 10,970 (1,257 free) | 50.5 | 100 % | 2 | 0.613 | 1/10 |

**The answer to the original question, at the resolution where dev actually runs: dev is mid-pack, not the
champion.** It beats klein, ties schnell within noise, and **loses to Z-Image on labels, paths, speed and VRAM**
- while still breaching the floor at 424 MiB free. Whatever earns dev its reputation was not reproducible on
this card at either resolution: at 1024 it does not render, and at 512 it does not win.

## The trap this run exposed, recorded so it is not repeated

**OCR at 512 is not comparable to OCR at 1024, and cross-resolution comparison is therefore invalid.**

| | OCR exact at 1024 | OCR exact at 512 |
|---|---|---|
| method B (code) | **0.876** | **0.261** |
| Z-Image | 0.811 | 0.786 |
| klein | 0.614 | 0.613 |

Code's source labels are **100 % at both resolutions** - the same SVG, rendered at half size, simply stops being
legible to the OCR. So the 512 numbers rank **diffusion arms against each other** and nothing else; anyone
comparing "code 0.261" against "Z-Image 0.786" would read a rendering artefact as a model difference. The
protocol therefore records **resolution as a first-class variable**, and the 1024 corpus remains the reference
because teaching diagrams need pixels for their text.

Two corollaries worth keeping:

- **The VRAM floor is resolution-dependent too.** Z-Image at 512 peaks at **9,269 MiB (2,958 free)**, comfortably
  passing, where at 1024 it peaks 11,165-11,794 MiB and is marginal. The same model is admissible or not
  depending on output size, so a floor claim must name the resolution.
- **Path economy scales with resolution** (code 69 -> 24, Z-Image 28 -> 31.5): fewer pixels, fewer glyph contours,
  so path counts are comparable only within one resolution.

## What stands

1. **Z-Image-Turbo is the best local diffusion model for this job at both resolutions** measured (0.811 @1024,
   0.786 @512) and the cheapest to run (2.0-5.1 s), and at 512 it clears the VRAM floor with 2.9 GB to spare.
2. **FLUX.1-dev's reputation is not reproduced here in either configuration** - unusable at 1 MP, mid-pack at
   0.5 MP. Recorded as a measurement at 512 and as a **blocked** arm at 1024, not as a refutation of the model
   family in principle.
3. **Code (method B) remains the production path** for teaching diagrams: 100 % labels in source, editable SVG,
   and the best structure and didactic scores measured at 1024.
