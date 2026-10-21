# Exact-token re-score of the historical arms, 2026-10-21

The label matcher was crediting substrings (`7` inside `70`, `120` inside `1120`). Every arm in this repo predated
the fix, so the two strong historical arms were re-scored from the **PNGs already on disk** — no inference, no new
renders, the same `raster_measure`/`trace_score` path with the corrected matcher.

| model (2 seeds, 10 prompts each) | substring (as published) | **exact token** | delta |
|---|---|---|---|
| Z-Image Turbo | 0.811 | **0.798** | −0.013 |
| FLUX.2-klein-4b Q4_K_M | 0.614 | **0.595** | −0.019 |
| FLUX.1-schnell Q4_K_S | 0.552 | **0.428** | **−0.124** |

**The ranking does not change.** Z-Image remains the strongest on labels by a wide margin, klein second, schnell
third. The published conclusions about model quality stand.

## Where the inflation actually was

| task | Z-Image | klein | schnell |
|---|---|---|---|
| tree-1 (7 single-digit labels) | 12/14 -> **12/14** | 9/14 -> 8/14 | 9/14 -> **1/14** |
| bar-1 | 8/8 -> 8/8 | 4/8 -> 4/8 | 4/8 -> 4/8 |
| flow-1 | 8/8 -> 8/8 | 6/8 -> 6/8 | 6/8 -> 6/8 |
| network-1 | 5/8 -> 5/8 | 6/8 -> 5/8 | 2/8 -> 2/8 |
| blueprint-1 | 0/4 -> 0/4 | 1/4 -> 1/4 | 0/4 -> 0/4 |

The whole schnell difference is **one task**: tree-1 fell from 9/14 to 1/14. Its digits were being "read" out of
coordinate text. Z-Image is unaffected on tree-1 (12/14 both ways) — it genuinely renders the seven digits, which
also explains why its score barely moved.

So the defect was not evenly distributed noise: it was a **specific vulnerability of one model on one task**, and
that is exactly the case where a substring matcher turns a failure into a near-pass.

## What this changes

- **Nothing about the model ranking.** Z-Image 0.798 > klein 0.595 > schnell 0.428 on labels, same order as before.
- **Schnell's headline number** drops by a quarter, and its tree task is now shown to agree with the ladder
  experiments: it does not bind digits at any density (1/14 here, 1/7 boundary-stated, 0/7 schema).
- **Tree-1 is a valid discriminator after all** — not "unmeasurable for every model", but unmeasurable *for schnell*.
  Z-Image scoring 12/14 means the task is measurable; the earlier blanket statement was too strong and is corrected.
- `result-arm-{zimage,klein,flux}-exact-scores.json` are the comparable artifacts. The originals are kept as the
  published record and must not be mixed with re-scored numbers.

## The 512-px arms, and what they overturn

Re-scored the same way (exact tokens, PNGs on disk):

| arm (512 px) | substring | **exact** | delta |
|---|---|---|---|
| FLUX.1-schnell Q4_K_S | 0.749 | **0.749** | 0.000 |
| FLUX.1-dev fp8 | 0.704 | **0.704** | 0.000 |
| Z-Image Turbo | 0.786 | **0.736** | −0.050 |
| FLUX.2-klein-4b | 0.613 | **0.565** | −0.048 |
| code-512 (Method B) | 0.261 | **0.261** | 0.000 |

Three arms are completely unaffected, which is itself the evidence that their labels are genuine: with no substring
credits to remove, the numbers cannot move.

**And then the finding that matters more than the matcher.** FLUX.1-schnell scores **0.749 at 512** against
**0.428 at 1024**, and the whole gap is the same task: tree-1 reads **4/7 at 512** and **1/14 at 1024** (two seeds).
Z-Image reads tree-1 at 6/7 (512) and 12/14 (1024); klein 4/7 and 8/14; dev 2/7 at 512.

So **label binding is resolution-specific, not a property of the model.** That overturns the sentence I wrote an hour
ago ("schnell cannot bind digits at any density"): at 1024 it cannot, at 512 it can. Every density experiment today
ran at 1024, so the "budget of ~10 numbers" is a statement about **1024 only** and its interaction with resolution is
now an open question rather than a settled one. The honest form of the conclusion is:

> At 1024 px, FLUX.1-schnell's label binding collapses (1/14 on tree-1) and coordinate prompts make it worse; the
> numeric budget measured today (peak ~10, leakage ~18, collapse ~34) applies at 1024. At 512 px the same model
> binds 4/7 of the same labels with prompts that never changed, so resolution is at least as strong a moderator as
> prompt density, and it was not controlled for.

## What this does not establish

- Re-scoring measures labels only. Path economy, axis alignment and trace fidelity were never affected by the
  matcher and are unchanged.
- The re-score is exact-token on the *same* OCR output, so it cannot recover a label the reader never saw; it only
  removes credits that were never earned.
- `render/zimage-512`, `klein-512`, `schnell-512`, `dev-512`, `code-512` (the 512-px arms) were **not** re-scored.
  Their results carry the same caveat and would need the same treatment before being quoted.
