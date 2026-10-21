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

## What this does not establish

- Re-scoring measures labels only. Path economy, axis alignment and trace fidelity were never affected by the
  matcher and are unchanged.
- The re-score is exact-token on the *same* OCR output, so it cannot recover a label the reader never saw; it only
  removes credits that were never earned.
- `render/zimage-512`, `klein-512`, `schnell-512`, `dev-512`, `code-512` (the 512-px arms) were **not** re-scored.
  Their results carry the same caveat and would need the same treatment before being quoted.
