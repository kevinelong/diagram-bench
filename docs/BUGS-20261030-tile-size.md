# Three sizing defects, 2026-10-30

Reported while preparing the app to be shared with a non-engineer. All three are about size,
none about behaviour; the app is otherwise sound.

## Open: three sizing defects (reported 2026-10-30, manual testing)

### B1 - strip tiles are sized by a constant, not by the viewport

`STRIP_TILE_MIN` is a floor, but the tile also never grows to use the column.

Ask: the second column should fit **12 pictures** (4 across x 3 down) before it scrolls.

Fix: from the canvas size, `tile = min((w - gaps) / 4, (h - gaps) / 3)`, clamped to a floor
(~160) and a cap (~320) so an ultra-wide does not produce absurd tiles. Keep the 96 px floor
only as a never-shrink-below for tiny windows.

Where: `zimage_ui.py` `_strip_metrics` / `_apply_strip_resize` / `_render_*`.

### B2 - "Ranked by wins" tiles are miniscule and capped at 8

`_review_finish` shows up to `cols * 2` = 8 tiles at 192-360 px.

Ask: same 4x3 = 12 geometry as B1, and **scroll** rather than truncate when a folder ranked
more than 12 - the ranked order matters, so hiding the tail is the wrong trade.

Where: `zimage_ui.py` `_review_finish`.

### B3 - folder column does not reserve the scrollbar's width

The pane's width does not include the vertical scrollbar, so the second folder column's right
edge is hidden under it.

Ask: reserve the scrollbar width in the initial column width and re-apply on resize.

Where: `zimage_ui.py` `CONTENTS_W` / `contents_holder` layout, `_on_contents_resize`.

## Acceptance for all three

- strip: >= 12 tiles fully visible in a normal window, tile >= 160 px, no clipping
- finish: tile larger than a strip tile, >= 12 slots, scrolls beyond that
- folder column: second column fully visible, scrollbar not overlapping it
- screenshots at 1x and 2x DPI for both screens (judge "barely fit 12" visually)
- all 13 suites green, especially `test_gallery_ui.py` (883 checks): tile sizing must not
  regress `takefocus`, the accent focus ring, or the strip's preserved scroll position
  (commit `5c02ad1` fixed exactly that).
