# A3: the Z-Image VRAM floor — `--cache-none`, not `--reserve-vram`
## 2026-10-21 — Z-Image Turbo nvfp4, 1024, prompt `net-1-words` for the flag test; the full frozen corpus for the re-run

Declared in `DECLARATION-20261021-a3-vram-floor.md` before any load. The floor is **>= 0.5 GiB free VRAM**; today's
Z-Image arms peaked at 11,700 MiB of 12,227 (~527 MiB free), which clears it by 15 MiB.

## The four flag variants (one image each, free VRAM at peak)

| variant | flags | free at peak | image wall | sha |
|---|---|---|---|---|
| v0 baseline | — | **961 MiB** | 12.1 s* | 575bf60d… |
| v1 | `--reserve-vram 1.0` | **950 MiB** | 7.2 s* | b33437ad… |
| v2 | `--cache-none` | **5,920 MiB** | 8.1 s* | 84c6807f… |
| v3 | `--lowvram` | **1,982 MiB** | 7.1 s* | a7e721bc… |

\* wall includes model load, so it is not comparable between variants; the corpus re-run below gives the real cost.

`result-a3-vram-floor.json`.

**`--cache-none` is the fix: it frees ~5.0 GiB.** **`--reserve-vram 1.0` is inert on this build** — 950 vs 961 MiB,
i.e. the flag whose help text promises exactly this does nothing measurable here.

## Re-run of the real arm under `--cache-none`

20 images, frozen corpus, seeds 1234+5678:

| arm | `ocr_exact_mean` | paths_median | axis_median | full-match | peak VRAM | wall/image |
|---|---|---|---|---|---|---|
| baseline | 0.786 | 465.5 | 95.1 | 12 | **11,700 MiB** | **3.1 s** |
| `--cache-none` | **0.786** | **465.5** | **95.1** | **12** | **6,255 MiB** | 7.1 s |

**Quality is identical on all four metrics and VRAM usage halves.** The cost is wall time: **3.1 -> 7.1 s per
image**, a 2.3x slowdown. So the floor can be bought, but it is paid for in seconds, not free.

## A control I had to add, and what it invalidates

The single-image test showed four different sha256 values across the four variants, which *looks* like the flags
changing the drawing. **Two identical baseline starts also produce different bytes** (68c07ad3… vs 8d534af0…), so:

- **this stack is not bit-reproducible across server starts**, and sha256 **cannot** be used to detect a flag's
  effect here;
- the four shas in the table above are noise, not evidence;
- the only sound comparison is an aggregate over many images, which is what the 20-image re-run provides — and
  there the four metrics are identical to three decimals.

That also means my own prediction 3 ("cache-none moves free VRAM by < 200 MiB") was wrong by an order of magnitude,
and prediction 2 ("reserve-vram is the fix") was wrong outright.

## Against the recorded predictions

1. **Baseline reproduces ~500-540 MiB free** - **WRONG**: a single image peaks at 11,266 and leaves **961 MiB**. The
   527 MiB figure came from the 20-image arms (two seeds), so peak scales with sustained load, not one image.
2. **`--reserve-vram 1.0` is the fix** - **WRONG**: 950 MiB, no change.
3. **`--cache-none` moves free by < 200 MiB** - **WRONG**: +4,959 MiB.
4. **`--lowvram` clears >= 2,000 MiB but costs >= 2x wall** - **half right**: 1,982 MiB free (just under the bar),
   no measured slowdown in the single-image test.

## Recommendation

- **Declare `--cache-none` for any Z-Image arm on this box**: it turns a 527 MiB margin into ~5.9 GiB and costs
  2.3x wall time. If throughput matters more than headroom, keep the baseline and treat the floor as met by
  15 MiB - but state that rather than calling it comfortable.
- **Do not use `--reserve-vram` on this build.** It is recorded as ineffective here so nobody spends another run on it.
- **Never judge a flag by a single image's hash** on this stack; use an aggregate over the full corpus.
