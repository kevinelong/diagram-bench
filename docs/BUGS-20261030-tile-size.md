# Three sizing defects, 2026-10-30

Reported while preparing the app to be shared with a non-engineer. All three are about size,
none about behaviour; the app is otherwise sound.

## Open: six defects, three of them about size (reported 2026-10-30, manual testing)

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

### B4 - the picture lists ignore the sort mode, so a rank never reaches them

`_visible_files()` ends with

    files.sort(key=lambda f: (f.name not in self._favorites, -f.stat().st_mtime))

and never consults the sort mode, so `Wins` and `Sequence` are options that cannot change anything: the picker fires
`_resort()` on change and `review_stop` selects `Wins`, but the order underneath is always favourites-first by mtime.
The only place a win is honoured today is the sheet walk (`key=lambda t: (-self._wins_of(t[1]['path']), -t[0])`).

Ask: `Wins` orders by wins desc, losses asc, mtime desc, name - with favourites as the *last* tie-break, not the
first, so a star cannot outrank a win. `Sequence` returns `_sequence_order(files)`. `Recent` (and anything else)
keeps exactly what it does now.

Where: `zimage_ui.py` `_visible_files` / `_wins_of` (memoised record).

### B5 - a tile does not show in the list while it is generated, and a small folder's tile is mostly empty

Reported in the same session: "layout n-up should show the images appear as thumbnails in the image list as each is
generated - of course it should immediately show in the image list when one new one is added - and if the folder does
not have 4 thumbnails it should show up there also."

Measured before the fix, 40 pictures + one written mid-run, folders on:

- the `'tile'` event already calls `refresh_gallery()`, and `_files` grew 40 -> 41 at once, but the strip was drawing
  *folder tiles*, so the new picture had nowhere to appear: `plan()` keeps the newest 18 pictures in `recent` and out
  of every folder by design, and the folders view draws only `plan()['groups']`.
- `_folder_sheet` always composed a 2x2: a folder with one picture drew it into one quarter of the tile, leaving three
  empty quarters (`sheet_bg`), 1 of 4 cells filled; 2 -> 2 of 4; 3 -> 3 of 4.

Fix:
- the strip lists the *pictures* while a run is in flight (`_folders_view_active()` is false while `busy`), so each
  tile shows the moment it is written; the folders return when the run ends and the plan is recomputed.
- `_sheet_cells` lays a sheet out for however many pictures the folder holds: 1 is the whole tile, 2 side by side,
  3 as two on top and a wide one below, 4 the 2x2 the tile was designed around. Each covers the tile exactly.

Not changed, and called out rather than slipped in: a one-picture folder still does not exist (`MIN_GROUP = 2`) -
that picture lives in `recent` - and after a run the newest 18 pictures are still not drawn in the folders view. Both
are `group_prompts.plan()` policy, not rendering; changing `recent` or `min_group` needs a decision of its own.

### B6 - the strip flickers while tiles land: the rightmost column expands and collapses

Reported: "as rendering completes and the image list collapses and redraws the right most column expands briefly and
then collapses again causing distracting flickering."

Mechanism: `_apply_strip_resize` added and removed the strip's scrollbar according to whether the content was taller
than the viewport. Removing it widened the canvas by the bar's 14 px, `_strip_metrics()` recomputed the tile from that
width, the tiles grew, the content got taller, the bar came back, the canvas narrowed - one oscillation per
`refresh_gallery()`, and a run calls that on every tile.

Fix: the bar keeps its grid cell and is only *disabled* when everything fits (`_set_bar`), for both columns - the
folder pane had the same toggle. The canvas width, and therefore the tile, cannot oscillate. Measured across six
tile events at 1707x937: `(canvas, cols, tile, bar)` identical on every refresh - 688, 4, 162 - where before the bar's
presence alternated.

## Acceptance

- strip: >= 12 tiles fully visible in a normal window, tile >= 160 px, no clipping
- finish: tile larger than a strip tile, >= 12 slots, scrolls beyond that
- folder column: second column fully visible, scrollbar not overlapping it
- screenshots at 1x and 2x DPI for both screens (judge "barely fit 12" visually)
- flicker: canvas width, column count and tile are constant across a synthetic batch (measured, `test_sizing.py`)
- tile: a picture written mid-run is in `_files` and painted in the strip with folders on; a 1/2/3-picture folder's sheet fills its tile
- sort: `Wins` returns the list in wins order, `Sequence` in film order, `Recent` unchanged
- all 13 suites green, especially `test_gallery_ui.py` (883 checks): tile sizing must not
  regress `takefocus`, the accent focus ring, or the strip's preserved scroll position
  (commit `5c02ad1` fixed exactly that).
