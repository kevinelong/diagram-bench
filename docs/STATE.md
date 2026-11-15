# State

**The suite is 1319 checks** (1319 passed, 0 failed, 0 skipped on this desktop) across the three window
suites - `test_gallery_ui.py` 1078, `test_sizing.py` 213, `test_tiles.py` 28 - and
`check_doc_counts.py` fails when this sentence and the run disagree.
: the layout's and the ranking's decisions, and what the suite now encodes (0.33.0, 2026-11-14)

This page is the durable half of the state record. The dated `STATE-YYYY-MM-DD.md` files at the root are the running
log of what was done on a day and what it measured; this one lists the **decisions the tests now enforce**, so a change
of policy has one place to be noticed in. It is deliberately short and is meant to be edited, not appended to.

## The four columns and their boundaries

| decision | when | where the code says it | what enforces it |
|---|---|---|---|
| the strip and the pane have **no floor (0)**; the sash stays grabbable at the edge, and double-click / Home / End restore a boundary without a magnifier | 2026-11-02 | `_column_floors` | `test_sizing.py`: `strip\|pane stops with the pane at its floor, which the policy says is nothing`, `and at the other end the gallery column is its two floors and the prompt has the rest` |
| the prompt's floor is its **measured control rows**, because a row of comboboxes cannot reflow | 2026-10-31 | `_prompt_floor` | `gallery\|prompt stops at the prompt's own measured floor`, `which is what its control rows need` |
| the picture column keeps a **measured 283 px** floor: its action bar clips below that | 2026-10-30 | `PICTURE_MIN_W` | `prompt\|picture stops at the picture's floor` |
| **no caps** where the reader is steering - a column grows until the window stops it, and the tile grid reflows | 2026-11-02 | `_column_caps` | `and no column is held by a cap: the pane takes the same share as the other three` |
| floors that would together exceed the window are **scaled to fit** so a drag cannot jam at low == high | 2026-11-02 | `_column_floors(width=)` | `test_gallery_ui.py`: `floors shrink to fit a narrow window instead of exceeding it` |
| a remembered width of **0 is a width**, not "never set" | 2026-11-14 | `_px_setting` | `a fresh start reads the same three boundaries back`, `so a window that opens on that file starts with a layout the reader set` |
| the documents' counts are **measured against the suites**, never remembered | 2026-11-14 | `check_doc_counts.py` | the three window suites print their own total, the checker sums them and fails naming the file, the line, the number claimed and the number measured; the sentence at the top of this page is one it reads |
| a check this box **cannot answer** is `SKIP`ped with its reason, and is in neither count | 2026-11-14 | `test_sizing.py`: `skip()`, `hit_area()` | the summary line prints the skip count and the tail names every skipped check and why; the screen-coordinate hit-area probe skips, rather than failing, on a desktop that answers `winfo_containing` for nothing |

The pane's `CONTENTS_W_MIN` (180) and `CONTENTS_W_MAX` (480) survive only as the **stored default** width and as the
starting value on a bad argument. They are not limits any more, and any clamp that still reads them is a bug: the
`480` clamp left in `_sash_apply` after the caps were removed is what snapped the first motion of every drag on the
strip|pane boundary back to 480 px (found 2026-11-14 by driving the drag through the binding, fixed in 0.31.0).

## The rank order: one function, and the evidence beside it (0.33.0)

`order: Wins` used to be two hand-written sort keys - one in `_sheet_paths` (the strip's folder tile) and one in
`_render_contents` (the pane) - and both ranked on raw wins. They are gone. The tile, the pane and the strip's flat list
now ask `pair_rank.order(paths, tie_break=<their own newest-first pass>)`, so there is one definition of "best" and no
surface can drift from another. The order is **fitted Bradley-Terry strength first** (`bt_strength`, fitted from the
per-opponent tallies in the sidecars), then the plain record: **most wins, fewest losses, then the name**. Strength is
what raw counts cannot express - five wins over a picture that never wins must not outrank winning a head-to-head
against the picture that beats everyone - which `test_pairs.py` now pins directly ("strength beats count: five wins over
a picture that never wins do not outrank winning the head-to-heads").

**The thin-record rule.** A picture with fewer than `MIN_DECIDED_BOUTS` (**3**) decided bouts - `wins + losses`, not
`bouts`, which counts ties - has no fit worth trusting: one or two results can drive a fitted strength to the floor. Such
a picture is ordered *after* every picture that has a fit, by its own record. One comparison is therefore never
presented as a ranking, and a thin record never sinks below a picture nobody has compared at all.

| what it settles | why |
|---|---|
| the tile/pane badge: `12W-1L (13)`, `1W (2)`, `''` | the parenthesis is the evidence: two wins out of two is not twelve out of thirteen. No bout at all means no badge, and the window's review hint speaks the same text |
| `rank_key` = `(-wins, losses, name)` | the *tail* of `order`, not an order of its own. Its old docstring claimed the win rate "protects against participation bias": it never did - the rate was the second key, so a 12W-1L beat a 3W-0L on wins alone, and with wins equal the rate and the loss count agree. The name is last because pictures written inside one second tie on mtime, and a stable sort then keeps whatever order the caller passed in |
| pairing is **untouched** | `pair_review.pair_up` and `EXHAUSTIVE_MAX` are exactly as they were. This changes what "best" means after a review, never which pictures a review offers |

The two keys were also the reason the tile and the pane could disagree (reported 2026-10-31), so the surface test is
about agreement rather than about a number: `test_gallery_ui.py`, "the pane and a folder tile ask one function, so they
cannot disagree about the best four" - a five-picture ladder whose strength runs oldest-first, so recency, raw counts and
the input list each give a different answer from the fit.

## The boundary itself (the four properties of a good divider)

1. **Explicit pointer capture.** `Sash._press` calls `grab_set` and records that it succeeded; `_motion` is guarded by
   `grab_current() is self`; `_release` gives it back. A drag whose capture was never taken still works through Tk's
   implicit grab, so a window that cannot be grabbed cannot strand a drag.
2. **One relayout per frame.** `App._sash_begin` (the press) records the starting widths and *does not apply*; the
   first motion applies immediately (1:1 from the press); every motion after that sets a 16 ms target whose last value
   is applied by the frame timer; the release flushes. Interiors (title reflow, prompt wrap, window minimum) are
   deferred to the release, the grid is not.
3. **The hit area is a target, so it scales.** `sash_px()` -> `App._sash_w`: 8 px at 96 dpi, 12 px at 144 dpi, and
   every place that counts a sash (three widgets, two grid minsizes, `_layout_chrome`, `_pane_total_w`) reads that one
   number. `_apply_sash_scale()` re-reads it.
4. **The keyboard contract.** Left/Right 16 px (64 with Shift), Home/End to the ends of the range, double-click to the
   even split - all through the widget's own bindings, each returning `break`.

`test_sizing.py` measures each of the four through the bindings rather than by calling the handlers, and
`notes/2026-11-14-sash-capture-and-coalescing.txt` has the numbers (40 motion events -> 3 relayouts, against 41 at
0.30.1).

## What a test may assume about the screen

The app opens on the **folders** screen (2026-11-03), where the open-folder pane, the prompt column and two of the
three sashes are `grid_remove`d on purpose. A block that measures those columns, the sashes or the strip's picture tiles
must put the window on a screen that has them - `on_screen(root, app, 'columns')` in `test_sizing.py`, the same switch
a click makes. Twelve of the layout block's checks were measuring removed widgets for months because of this, and B3
stalled for a day in it (`notes/2026-11-14-test_sizing-B3-diagnosed.txt`). The test's assertion is not the thing to
weaken here; the screen is the thing to set.

## The suites and their numbers (this desktop, 2026-11-14)

    python test_sizing.py      exit 0  ALL PASS  77 s   209 PASS, 24 blocks, 0 failures
    python test_gallery_ui.py  exit 0  ALL PASS  57 s
    python test_tiles.py       exit 0  ALL PASS   2 s

One Tk test at a time: they take the desktop. `test_sizing.py`'s waits are bounded (`DRAIN_CAP` 4 s, `LOOP_CAP` 0.5 s)
and a step that cannot finish says so as `<block> blocked: ...` instead of hanging.
