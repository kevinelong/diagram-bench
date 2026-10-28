# Retrospective on the existing gallery, 2026-10-28

Gallery: `C:\Users\kevin\Pictures\zimage-ui` - 1945 pictures, 1907 sidecars. Every number below is computed from those files by `retrospect.py`; nothing is estimated and no future field is assumed.

### A. Integrity

| what | count |
|---|---|
| pictures on disk | 1945 |
| sidecars | 1907 |
| pictures with no sidecar | 44 |
|   oldest such picture | 20261022-153133_1024x1024_seed123 |
| sidecars with no picture (6), of which clip sidecars | 6 |
| unreadable sidecars | 0 |
| pictures that will not decode | 0 |
| recorded size is a contact sheet's tile size, not the file's | 44 |
| size box ignored: the file is the base image's size (and the 2026-10-22 grid tiles) | 33 |
| recorded size disagrees with the pixels after that | 0 |

Sizes: 1024x1024 x1395, 256x256 x427, 512x512 x72, 1042x1042 x13, 780x780 x11, 4114x4114 x7, 3084x3084 x5, 518x518 x4

| byte-identical pictures | 0 in 0 groups |

### B. Generation history

| day | renders |
|---|---|
| 2026-10-22 | 441 |
| 2026-10-23 | 608 |
| 2026-10-24 | 97 |
| 2026-10-25 | 186 |
| 2026-10-26 | 432 |
| 2026-10-27 | 90 |
| 2026-10-28 | 53 |

Models, canonicalised (spellings folded, and the fold is in `canonical_model`):

- z-image turbo: 1459
- flux2 klein 9b: 439
- unknown: 6
- flux1 schnell (legacy): 2
- flux1 dev (legacy): 1

Steps: 4 x873, 12 x676, 2 x278, 9 x30, 50 x20, 6 x15, 8 x8, 30 x6, 20 x1

**Steps above the model's distilled ceiling: 699 renders**
- z-image turbo at 12 steps (ceiling 9): 499
- flux2 klein 9b at 12 steps (ceiling 4): 177
- flux2 klein 9b at 50 steps (ceiling 4): 14
- z-image turbo at 50 steps (ceiling 9): 6
- flux2 klein 9b at 8 steps (ceiling 4): 3

cfg: 1.0 x1890, 3.0 x10, 5.0 x6, 3.5 x1

Denoise on base renders: 0.2 x1, 0.28 x1, 0.3 x2, 0.36 x2, 0.39 x1, 0.4 x1, 0.45 x1, 0.48 x4, 0.5 x1, 0.54 x3, 0.56 x1, 0.59 x5, 0.6 x92, 0.61 x1, 0.62 x1, 0.63 x3, 0.64 x1, 0.65 x1, 0.66 x3, 0.67 x1, 0.68 x14, 0.69 x6, 0.7 x30, 0.71 x2, 0.72 x10, 0.73 x3, 0.74 x40, 0.75 x4, 0.76 x23, 0.77 x9, 0.78 x3, 0.79 x14, 0.8 x24, 0.81 x10, 0.82 x1, 0.83 x6, 0.85 x13, 0.87 x28, 0.91 x1, 0.94 x1, 0.95 x53

Distinct seeds: 929; most repeated: 1235 x96, 1234 x76, 1231 x42

### C. The prompt corpus

| measure | value |
|---|---|
| prompts | 1901 |
| distinct prompt strings | 687 |
| words per prompt: mean / median / max | 233.3 / 67 / 3128 |
| prompts under 6 words | 247 |

| prompts within 0.90 cosine of another | 28304 pairs |
| distinct prompts once 0.90 near-duplicates are merged | 238 |
| vocabulary (after the module's stop-and-style stripping) | 3702 terms |

Most repeated 4-grams (a phrase in 3+ prompts means the corpus is built on it):

- "center the three in" x327
- "the three in the" x327
- "three in the middle" x327
- "frame and show the" x326
- "and show the environment" x326
- "show the environment all" x326
- "the environment all around" x326
- "from a distance center" x325

### D. Lineage, measured in pixels

| measure | value |
|---|---|
| pictures with a base | 421 |
| chain depth: 2 / 3-5 / 6-10 / deeper | 88 / 110 / 130 / 93 |
| deepest chain | 45 |

Pairs decoded and compared at 64x64 grey: **416**

| denoise | pairs | mean pixel difference | mean block difference |
|---|---|---|---|
| 0.20 | 1 | 0.0068 | 0.0033 |
| 0.28 | 1 | 0.0077 | 0.0037 |
| 0.30 | 2 | 0.0541 | 0.0218 |
| 0.36 | 2 | 0.0392 | 0.0164 |
| 0.39 | 1 | 0.0809 | 0.0552 |
| 0.40 | 1 | 0.0862 | 0.0411 |
| 0.45 | 1 | 0.1369 | 0.1113 |
| 0.48 | 4 | 0.0803 | 0.0479 |
| 0.50 | 1 | 0.0306 | 0.0116 |
| 0.54 | 3 | 0.0336 | 0.0173 |
| 0.56 | 1 | 0.0776 | 0.0540 |
| 0.59 | 5 | 0.0308 | 0.0129 |
| 0.60 | 91 | 0.0378 | 0.0197 |
| 0.61 | 1 | 0.0762 | 0.0537 |
| 0.62 | 1 | 0.0464 | 0.0187 |
| 0.63 | 3 | 0.0492 | 0.0287 |
| 0.64 | 1 | 0.0279 | 0.0165 |
| 0.65 | 1 | 0.0783 | 0.0623 |
| 0.66 | 3 | 0.0668 | 0.0511 |
| 0.67 | 1 | 0.0773 | 0.0623 |
| 0.68 | 14 | 0.0848 | 0.0583 |
| 0.69 | 6 | 0.0682 | 0.0359 |
| 0.70 | 29 | 0.1060 | 0.0684 |
| 0.71 | 2 | 0.0418 | 0.0268 |
| 0.72 | 10 | 0.0671 | 0.0421 |
| 0.73 | 3 | 0.0563 | 0.0382 |
| 0.74 | 40 | 0.1126 | 0.0590 |
| 0.75 | 4 | 0.1510 | 0.0896 |
| 0.76 | 23 | 0.0796 | 0.0497 |
| 0.77 | 9 | 0.0940 | 0.0674 |
| 0.78 | 3 | 0.0903 | 0.0527 |
| 0.79 | 14 | 0.0812 | 0.0474 |
| 0.80 | 24 | 0.1343 | 0.0712 |
| 0.81 | 10 | 0.1366 | 0.1020 |
| 0.82 | 1 | 0.0263 | 0.0104 |
| 0.83 | 6 | 0.2741 | 0.2295 |
| 0.85 | 13 | 0.1657 | 0.0928 |
| 0.87 | 28 | 0.1872 | 0.1106 |
| 0.91 | 1 | 0.2316 | 0.1843 |
| 0.95 | 51 | 0.2370 | 0.1533 |

Control - unrelated pairs: mean pixel difference **0.1383** over 60 pairs.

A variation becomes as different as two unrelated pictures from **denoise 0.75** upward; below that it is a near-copy of its parent (55 % of the base renders sit under 0.75).

| the child's prompt vs its parent's | count |
|---|---|
| identical | 166 |
| parent contained in child (appended) | 40 |
| child contained in parent (shortened) | 2 |
| rewritten | 208 |
| parent sidecar missing or empty | 5 |

### E. The folders, scored retrospectively

| measure | value |
|---|---|
| folders | 85 |
| pictures in folders | 1862 |
| kept ungrouped: newest 18 / no prompt | 18 / 21 |
| folder size: min / median / max | 2 / 7 / 280 |
| folders of 2-3 pictures | 26 |

| mean intra-folder similarity | 0.852 |
| folders below 0.5 intra-similarity | 3 |
| mean similarity of the label to its members | 0.752 |
| folders whose label fits below 0.7 | 28 |

| varied pictures: same folder as parent / different / not in a folder | 342 / 55 / 24 |

### F. What the existing data cannot answer

| missing | consequence |
|---|---|
| sampler | which sampler drew each picture - not recorded at all, so a sampler question is unanswerable |
| wins / rank | no picture carries a win, so no preference model can be fit |
| base_prompt | the parent's prompt is only reachable through the parent file |
| wall time per render | only the 6 clips record `wall_s`; image renders have no duration |
| VRAM peak per render | recorded for 6 clips only |
| rejects | deleting a picture leaves no trace, so the discarded half of the search is invisible |
| edits | a prompt that was edited and re-run is indistinguishable from a fresh one |

Runnable again from their own sidecar alone (prompt, model, steps, cfg, seed present): **1901 of 1907 (100 %)**; the rest predate the schema or were pasted from the clipboard.

Fields present in fewer than 70 %% of sidecars: negative (1288), style (1288), mask (1288), outpaint (1288), hires (1288), hires_denoise (1288), sweep (1275), unet (1275), batch (44), tiles (44), seeds (41), clip (6)

