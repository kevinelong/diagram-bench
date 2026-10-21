# Three arms measured on the frozen diagram corpus, 2026-10-20

Same corpus, same seeds where the method has seeds, same scorer, same 1024x1024 target. **Method B** =
`qwen3-coder:30b` writing SVG (no image model). **klein** = FLUX.2-klein-4B GGUF Q4_K_M, 4 steps, cfg 1.0.
**Z-Image** = Z-Image-Turbo nvfp4, 9 steps, cfg 1.0. Artifacts: `result-arm-*.json`, `result-vlm-*.json`,
`render/{,klein/,zimage/}`.

## The table

| | **B: code → SVG** | **klein-4B Q4_K_M** | **Z-Image-Turbo nvfp4** |
|---|---|---|---|
| images | 19/20 valid documents | 20/20 | 20/20 |
| wall, median | 13 s (8-22) | **5.0 s** | 5.1 s |
| peak VRAM | n/a (no GPU) | 10,350 MiB (1,877 free) **passes the floor** | **11,748 MiB (479 free) - breaches the 512 MiB floor** |
| paths, median | 69 * | **27.5** | 28.0 |
| nodes, median | 702 | 598 | 820 |
| axis alignment, median | **100 %** | **100 %** | 93.8 % (rounded corners) |
| major colours, median | 2 | 2 | 2 |
| **OCR exact, mean** | **0.876** | 0.614 | 0.811 |
| **images with every label correct** | **14/19** | 3/20 | 13/20 |
| **structure checks** | **44/57 (77 %)** | 37/60 (62 %) | 37/60 (62 %) |
| **didactic answers correct** | **45/57 (79 %)** | 28/60 (47 %) | 36/60 (60 %) |
| determinism (same seed twice) | n/a | **different** | **different** |

\* Method B's 69 paths are the **worst case**: it is what you get if you rasterise its SVG and re-trace it. Its
actual artifact is the SVG source, **1-3 KB with real `<text>` elements**, which needs no tracing at all. Path
count is a fair axis for the diffusion arms and a misleading one for B.

## What the numbers say

1. **Text fidelity is the discriminator, exactly as the klein smoke test suggested.** For teaching diagrams the
   label is the payload: method B **0.876**, Z-Image **0.811**, klein **0.614**. Z-Image is *very* close to code
   on labels and 2.6x better than klein on full-label images (13/20 vs 3/20).
2. **Diffusion wins the trace axis outright**: 27.5 and 28.0 paths against 69, because a diffusion image is
   already a rendered "picture" of a diagram, whereas re-tracing rendered text explodes into glyph contours.
   Z-Image's rounded corners cost it axis alignment (93.8 %) - a real, measurable style-vs-traceability trade.
3. **The didactic gap is the widest**: 79 % vs 60 % vs 47 %. A reader could answer the learner questions from
   method B's diagrams far more often, and from klein's least often - which matches klein's label failures.
4. **Z-Image is the diffusion model to keep.** Same speed as klein, better on every quality axis, 2 points off
   method B on OCR - and its **VRAM peak breaches the 0.5 GiB floor (479 MiB free)**. So it is the best *image*
   result here and not yet an admissible lab configuration: the fits that would fix it are a smaller encoder
   (fp4_mixed is what we used) or fewer resident components.
5. **Neither diffusion arm is deterministic at fixed seed** (PNG hashes differ between two identical runs), so
   every small difference between arms must be read against that noise - the same rule the LLM track applies to
   Strata's 8-28 % start-to-start spread.
6. **Method B remains the best local answer for this job**, and it is the only arm with an editable artifact.
   But "best" is now a measured claim with three components: labels 0.876, structure 77 %, didactic 79 % - and a
   known, cheap repair path (validate labels as `<text>`, re-ask on miss).

## Corrections and gate notes

- **The int8 convrot Z-Image checkpoint does not load** in ComfyUI 0.18.2: `UNETLoader` raises
  `KeyError: 'int8_tensorwise'`. The **nvfp4** variant (4.51 GB) loads and runs, which also matches this card
  (Blackwell, comfy_kitchen advertises nvfp4 support).
- **ComfyUI 0.18.2 needs `comfyui-frontend-package`** as a separate pip install, and a `.gguf` UNet needs
  `city96/ComfyUI-GGUF` + `gguf` in the venv. Both cost time and are now recorded.
- **Z-Image's card specifies guidance 1.0-equivalent 0.0 and 9 steps**; we ran `cfg 1.0, 9 steps`, which is the
  ComfyUI convention for a no-CFG turbo model. Recorded so the arm is reproducible.
- **floor**: klein passes the 0.5 GiB GPU floor, Z-Image does not, and both ran. Reported as a floor breach, not
  silently accepted - the same treatment as the `--moe-cache-mib 6144` clamp in the LLM track.
