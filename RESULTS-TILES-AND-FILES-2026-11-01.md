# Tiles and files, measured 2026-11-01

Everything below was measured tonight on this machine, on the real gallery
(`C:/Users/kevin/Pictures/zimage-ui`) unless a row says otherwise. The point of writing it down is that three of these
numbers contradicted what the code assumed.

## What counts as a picture

| | |
|---|---|
| Files on disk under `IMAGE_SUFFIXES` | **2,181** |
| Files the app lists | **2,181** |
| Formats Pillow 12.1 in the Comfy venv decodes | png, webp, jpg, jpeg, gif, bmp, tif, tiff, avif, ico |
| Formats it cannot | **jxl**, **heic** (needs `pillow-jxl` / `pillow-heif`) |
| Formats the app listed before | png, webp, jpg, jpeg - the rest were silently absent |

`ico` is not in the tuple on purpose: it is an application icon, not a picture.

## Where the invisible pictures were

| | Before | After |
|---|---|---|
| Files scanned | 2,160 | 2,160 |
| Open folder after a default open | 844-picture family (39 % of the gallery) | same family opened |
| Pictures reachable in the strip | **844** | **2,160** |
| Unfiled (no prompt -> cannot join a family) | 83, invisible | 87, reachable via the **no prompt** filter |
| Pasted reference screenshots | 7, all invisible | 7, all found |

Scoping the flat list to the open folder is what did it. It was introduced to make a selected chip "filter the strip",
and on a gallery where one family is 39 % of the files that is the same thing as hiding the rest.

## The tile ladder

| | Before | After |
|---|---|---|
| Distinct sizes over a 221-pixel width sweep | 221 | **10** (192, 208, ... 336) |
| PhotoImages minted by that sweep, 18 tiles | **3,978** | **180** |
| Cache bound | none | 400 entries (LRU) |
| Largest tile | 420 px (upscaled from a 336 px thumb) | 336 px (the thumbnail's own size) |
| 18 tiles at one size | 9.5 ms per tile re-made | **32 ms cold**, **0.0 ms warm** |
| `read_meta` | disk read + JSON parse, 20 call sites, several per tile | memoised by `(sidecar, mtime)` |

Rounding **down** to the rung matters: a Tk label sizes itself to its image, and rounding 152 up to 160 pushed the ninth
strip tile under the scrollbar (`test_sizing` caught it as +16 px).

## The loose family that would not split

| | Value |
|---|---|
| Largest family before | 844 pictures, median prompt **359 words** (max 1,962) |
| Its median intra-cosine | **0.509** (tight bar 0.60 -> a split candidate) |
| Re-cluster at threshold 0.30 | largest piece **81 %** of the family -> the split rule refused it |
| Thresholds now tried | 0.30, then 0.40, then 0.50, then give up |
| Real gallery, before -> after | folders **64 -> 80**, largest **844 -> 456**, unfiled 83 -> 87 |
| Accounted | **2,158 of 2,158** |
| Plan time on 2,158 pictures | 31 s |

The 456 that remain are the velvet-dress series: one scene, many versions, and correctly one folder.

## Tests

`test_tiles.py` is new and covers the four behaviours above (formats, the no-prompt filter and badge, the unscoped list,
the ladder and its bound, the `read_meta` memo, and a 180-picture long-prompt family splitting into six). Two
`test_gallery_ui` assertions and one `test_sizing` assertion encoded the old contracts and were rewritten with the
measurement that reversed them. All sixteen suites pass: `test_gallery_ui test_sizing test_caption test_caption_folder
test_describe test_enter test_groups test_lineage test_pairs test_refs test_review_modes test_scenes test_shotlist
test_sidecars test_video test_tiles`.
