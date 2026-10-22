# A3 declaration: the Z-Image VRAM floor, 2026-10-21

Declared before any load. The track's floor is **>= 0.5 GiB free VRAM**; today's Z-Image arms peaked at
**11,700 MiB of 12,227**, i.e. **~527 MiB free** - clearing the floor by 15 MiB. That is not headroom, it is luck.

## What is actually available

The "smaller encoder" idea is **not available without a download**: `text_encoders/` holds only
`qwen_3_4b_fp4_mixed` (3.48 GB, in use), `qwen_3_4b_fp4_flux2` (3.85 GB, larger), `clip_l` and `t5xxl_fp8`
(both wrong architecture for lumina2). The diffusion model is already the smallest Z-Image on disk (nvfp4 4.2 GB
against int8_convrot 5.8 GB). So the knob is **scheduling**, not components - and this ComfyUI build exposes it:

- `--reserve-vram N` (GB) - "amount of vram to reserve for OS/other software".
- `--cache-none` - reduced RAM/VRAM, every node re-executes.
- `--lowvram` / `--novram` - split the unet / go further.
- `--disable-smart-memory` - aggressive offload.

## Predictions, recorded before the load

1. **Baseline (no flags) reproduces ~500-540 MiB free** at peak, confirming today's 527 rather than assuming it.
2. **`--reserve-vram 1.0` is the fix**: free VRAM rises to **>= 1,000 MiB** with wall time within **10 %** of
   baseline, because the model still fits - the reserve only stops ComfyUI filling the last gigabyte.
3. **`--cache-none` alone moves free VRAM by less than 200 MiB**: the resident weights dominate the footprint, not
   the node cache, so caching is not where the floor is being lost.
4. **`--lowvram` clears >= 2,000 MiB free but costs >= 2x wall** - acceptable as a fallback, not as the default.

## Design

One fixed prompt (`net-1-words` from `prompts-density.json`), Z-Image Turbo nvfp4, 1024, seed 1234, 4 steps, one
image per variant. Same arm, same scorer-irrelevant output. Four server starts:

| variant | flags |
|---|---|
| v0 | none (today's configuration) |
| v1 | `--reserve-vram 1.0` |
| v2 | `--cache-none` |
| v3 | `--lowvram` |

Peak VRAM is sampled by the arm (`nvidia-smi` every 250 ms), so "free" = 12,227 - peak. Wall time per image is
recorded, and the output sha256 is kept so that any change in the drawing is visible rather than assumed away.
