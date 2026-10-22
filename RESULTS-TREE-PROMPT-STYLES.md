# Three ways to ask for the same binary tree, and what each one buys
## 2026-10-22 — Z-Image Turbo (nvfp4), 1024, 4 steps, cfg 1.0, seed 1234

One task (seven numbered circles in three levels, parent-child edges), three prompt styles. Same model, same seed,
same sampler. Labels scored by exact-token OCR (`ocr_check.py`); edges read from the image.

| style | chars | numeric tokens | labels | edges |
|---|---|---|---|---|
| **coordinate dump** (`prompts-diagrams-grounded.json`, tree-1 v2) | 1,947 incl. 153-char suffix | **90** | **7/14 over two seeds** — digits replaced by the prompt's own numbers (`70, 40, 66, 30, 212, 15`) | all 6 drawn, exactly |
| **boundary only** (`prompts-variants/tree-boundary.txt`) | 458 | 15 | 6/7 on this seed (the thin `1` defeated the reader; all seven are drawn) | **4 of 6** — `1-2`, `1-3`, `2-5`, `3-6`; nodes 4 and 7 orphaned, one faint stray stroke |
| **boundary + edge list** (`prompts-variants/tree-boundary-edges.txt`) | 513 | 24 | **7/7 exact** | ~6 connections: `1-2`, `1-3`, `2-4`, `2-5`, `3-6` (stops short of the node), `3-7`; arrowheads appear on two edges only |

## What this shows

**Spend the numbers on relationships, not on positions.** The model reproduces a *pattern* correctly from prose
("four in the bottom row, evenly spaced; two above the gaps; one at the top") — it does not need 90 coordinates. What
it gets wrong without being told is **who connects to whom**: the boundary-only prompt produced a clean layout and
then invented 4 of 6 edges, orphaning two nodes.

One sentence — `1 to 2 and 3; 2 to 4 and 5; 3 to 6 and 7` — cost 9 numeric tokens and moved labels from 6/7 to **7/7**
and edges from 4 to ~6.

And the coordinate dump, for 90 numbers, produced the **worst** result of the three: every node labelled with a
coordinate instead of its number. Precision there was paid for with the thing being measured.

## Residual defects in the winning variant

- Line styling is inconsistent: `2-4` and `3-7` carry arrowheads, the others are plain lines, and `3-6` stops short of
  its node. The prompt said "straight lines from each parent down to its own children" and did not forbid arrowheads.
- A small stray glyph sits between nodes 2/3 and 5/6 — the model's own residue, not a label.
- The canvas came out with a rounded dark border (a poster-like frame) that the prompt did not ask for.

## What this does not establish

- One seed, one sample per style. The 6/7 vs 7/7 difference is a single draw each; only the coordinate-dump result
  (7/14 over two seeds) has any replication behind it.
- Nothing about other tasks: this is the tree, where labels are single digits and therefore the hardest case for OCR.
