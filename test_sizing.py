#!/usr/bin/env python
"""test_sizing.py - the sizing and layout work of 2026-10-30, measured rather than eyeballed.

    python test_sizing.py

Covers `docs/BUGS-20261030-tile-size.md` and the two follow-ups of the same day:

* B1 - the strip's tile is the size at which three across and three down fit its canvas, nine visible;
* B2 - the finish screen is the same 4x3 grid, larger than a strip tile, and scrolls rather than truncating;
* B3 - the open-folder pane reserves its scrollbar and reflows instead of drawing past its edge;
* B4 - `Wins` and `Sequence` actually order the lists;
* the layout - four adjacent columns with a sash between every pair, an even first split, each boundary dragged
  independently, remembered and re-clamped, and the folder pane never collapsed (so a redraw cannot re-lay anything);
* the folder grid - three tiles per row rather than two, and every tile size derived from the column it is in.

It builds its own window and its own gallery under `tempfile.mkdtemp`, so nothing touches a real gallery or profile.
"""

import json
import os
import queue
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace as NS

from PIL import Image

os.environ.setdefault('ZIMAGE_SETTINGS_DIR', tempfile.mkdtemp(prefix='sizing-set-'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pair_rank  # noqa: E402
import pair_review  # noqa: E402
import zimage_ui as zui  # noqa: E402

pair_review.STATE_DIR = Path(tempfile.mkdtemp(prefix='sizing-state-'))

FAILS = []


def check(label, ok, detail=''):
    print('%-64s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    if not ok:
        FAILS.append(label)


class Stub:
    def __init__(self):
        self.events = queue.Queue()
        self.events.put(('ready', None))

    def generate(self, *a, **k):
        return True

    def make_clip(self, *a, **k):
        return True

    def cancel(self):
        return 'x'

    def shutdown(self):
        return 'x'


def make_gallery(folder, n=40):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    base = time.time() - 7200
    for i in range(n):
        p = folder / ('20261030-1200%02d_512x512_seed%d.png' % (i, 100 + i))
        Image.new('RGB', (320, 240), (20 + 9 * i % 210, 90, 210 - 8 * i % 200)).save(p)
        p.with_suffix('.json').write_text(json.dumps({
            'prompt': 'a cat number %d' % (i % 4), 'seed': 100 + i, 'created': '2026-10-30T12:%02d:00' % (i % 60),
        }), encoding='utf-8')
        os.utime(p, (base + i * 10, base + i * 10))
        paths.append(p)
    return paths


def pump(root, app, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        try:
            app._poll()
        except Exception:
            pass
        root.update()
        time.sleep(0.02)
    root.update()


def wipe_records(paths):
    """Take every picture's standing back to nothing: this suite writes wins of its own."""
    for p in paths:
        meta = pair_rank.read_meta(p)
        for key in ('wins', 'losses', 'ties', 'bouts', 'last_bout', 'vs'):
            meta.pop(key, None)
        pair_rank.write_meta(p, meta)


def cells(app, frame=None):
    frame = frame if frame is not None else app.recent
    return [w for w in frame.winfo_children() if w.grid_info()]


def right_edge(widgets):
    return max((w.winfo_x() + w.winfo_width() for w in widgets), default=0)


def bottom_edge(widgets):
    return max((w.winfo_y() + w.winfo_height() for w in widgets), default=0)


def strip_rows(widgets):
    """The first `STRIP_FIT_ROWS` rows of the strip: the nine it promises before it scrolls."""
    return [w for w in widgets if int(w.grid_info()['row']) < zui.STRIP_FIT_ROWS]


def drag(app, sash, dx, steps=8):
    """Drag a boundary through its own handlers: the press, the motion, the release, in that order.

    Tk's synthetic pointer events are fine for one drag but not for a second one on another sash in the same run - the
    motions are dropped once a grab has been handed between windows (measured: only the press arrived) - so the tests
    that assert arithmetic drive the handlers the bindings are wired to, and one event-driven drag below proves the
    bindings are wired at all.
    """
    x = sash.winfo_rootx() + sash.winfo_width() // 2
    sash._press(NS(x_root=x, state=0))
    for i in range(steps):
        sash._motion(NS(x_root=x + int(dx * (i + 1) / steps), state=0))
    sash._release(NS(x_root=x + dx, state=0))


def column_widths(app):
    """(strip, pane, prompt, picture) as the app has them, in content pixels."""
    return (app._strip_w, app._contents_w, app._prompt_w, app._layout_widths()[zui.COL_PICTURE])


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sizing-'))
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')
    paths = make_gallery(tmp / 'gallery')
    wipe_records(paths)

    root = tk.Tk()
    args = NS(port=8019, size=512, steps=4, cfg=1.0, seed=1, out=str(tmp / 'gallery'),
              initial='', initial_file='', auto=False, no_progress=True)
    app = zui.App(root, Stub(), args)
    pump(root, app, 1.2)              # the app starts maximised, which is the "normal window" the ask is about
    app.toggle_groups(False)          # the picture strip, not the folder list
    pump(root, app, 0.6)

    # ---- the layout: N columns, a sash between each pair, an even first split, the pane never collapsed
    print('\n-- layout: four adjacent columns with a sash between every pair')
    # Enumerated from the grid rather than assumed: the strip and the open-folder pane share the gallery column, and the
    # gallery, prompt and picture columns are the root's 0/2/4. The folded sidebar's rail replaces the gallery column,
    # it is not a fifth one.
    check('the layout has four columns', zui.COLUMNS == ('strip', 'pane', 'prompt', 'picture'), zui.COLUMNS)
    managers = [(app.contents_sash, 'grid'), (app.gallery_sash, 'grid'), (app.prompt_sash, 'grid')]
    check('and a sash at every boundary between them', all(s.winfo_manager() == m for s, m in managers),
          [(s.name, s.winfo_manager()) for s, _ in managers])
    check('each sash sits between the two columns it separates', (
        app.gal_canvas.winfo_rootx() + app.gal_canvas.winfo_width() <= app.contents_sash.winfo_rootx()
        and app.contents_sash.winfo_rootx() + app.contents_sash.winfo_width() <= app.contents_canvas.winfo_rootx()
        and app.gal.winfo_rootx() + app.gal.winfo_width() <= app.gallery_sash.winfo_rootx()
        and app.gallery_sash.winfo_rootx() + app.gallery_sash.winfo_width() <= app.text.master.winfo_rootx()
        and app.text.master.winfo_rootx() + app.text.master.winfo_width() <= app.prompt_sash.winfo_rootx()
        and app.prompt_sash.winfo_rootx() + app.prompt_sash.winfo_width() <= app.canvas.master.winfo_rootx()),
        [(s.name, s.winfo_rootx(), s.winfo_width()) for s in app.sashes])
    for s in app.sashes:
        check('%s: 8 px hit area, resize cursor, tab stop with an accent ring, a quiet 2 px grip' % s.name,
              6 <= s.winfo_width() <= 8 and s.cget('cursor') == 'sb_h_double_arrow' and bool(s.cget('takefocus'))
              and int(s.cget('highlightthickness')) > 0
              and str(s.cget('highlightcolor')) == app.theme.accent
              and s.grip.winfo_width() == 2 and str(s.grip.cget('background')) == app.theme.border,
              (s.winfo_width(), s.cget('cursor'), s.cget('takefocus'), s.grip.winfo_width()))

    # the split is even, subject to each column's own floor, and the leftover pixels go to the left on purpose
    content = app._content_w()
    floors = [app._column_floors()[c] for c in zui.COLUMNS]
    check('split_evenly divides evenly where no floor binds',
          zui.split_evenly(2400, floors) == [600, 600, 600, 600], zui.split_evenly(2400, floors))
    check('and gives the odd pixels to the leftmost column, deterministically',
          zui.split_evenly(2402, floors) == [601, 601, 600, 600]
          and zui.split_evenly(2402, floors) == zui.split_evenly(2402, floors),
          zui.split_evenly(2402, floors))
    check('a column whose floor the share would breach takes the floor instead',
          zui.split_evenly(1600, [524, 180, 526, 240]) == [524, 275, 526, 275],
          zui.split_evenly(1600, [524, 180, 526, 240]))
    live = column_widths(app)
    check('the live split is the even split, floor by floor', live == tuple(app._even_widths()[c] for c in zui.COLUMNS),
          (live, app._even_widths(), content))
    check('no column is below its stated minimum',
          all(live[i] >= floors[i] for i in range(4)), (live, floors))
    check('and the four columns plus their chrome are exactly the window',
          sum(live) + app._layout_chrome() == root.winfo_width(),
          (sum(live), app._layout_chrome(), root.winfo_width()))
    check('the root grid carries the same numbers as the columns plus their padding',
          [int(root.grid_columnconfigure(i)['minsize']) for i in (0, 2, 4)]
          == [app._gallery_col_w(), app._prompt_w + zui.PROMPT_PAD, live[3] + zui.PICTURE_PAD],
          [int(root.grid_columnconfigure(i)['minsize']) for i in (0, 2, 4)])
    check('every column has a floor that is stated and fits the window',
          app._layout_min_w() <= root.winfo_width(), (app._layout_min_w(), root.winfo_width()))
    check('the window refuses to be narrower than that minimum',
          root.minsize()[0] >= min(app._layout_min_w(), root.winfo_screenwidth() - 80),
          (root.minsize(), app._layout_min_w()))

    # at a known window width: shares that no floor or cap binds are equal, and the odd pixel is on the left
    real_root_w = app._root_w
    app._root_w = lambda width=None: 2560
    try:
        even_2560 = app._even_widths()
        wide = app._layout_widths(2560)
    finally:
        app._root_w = real_root_w
    free_w = [v for c, v in even_2560.items() if c != zui.COL_PANE]
    check('at a 2560 px window the columns that are not at a cap are equal to within one pixel',
          max(free_w) - min(free_w) <= 1, even_2560)
    check('and the pane is the one at its own cap, which is stated rather than accidental',
          even_2560[zui.COL_PANE] == zui.CONTENTS_W_MAX, (even_2560, zui.CONTENTS_W_MAX))
    check('the four columns are the content width in four, with the leftover on the left',
          sum(even_2560.values()) == 2560 - app._layout_chrome(), (even_2560, app._layout_chrome()))
    tile_2560 = (wide[zui.COL_STRIP] - app._bar_px(app.gal_bar)) // zui.STRIP_FIT_COLS - zui.TILE_CHROME
    check('and the strip is then wide enough for a 206 px tile', wide[zui.COL_STRIP] == even_2560[zui.COL_STRIP]
          and tile_2560 == 206, (wide[zui.COL_STRIP], tile_2560))

    # each boundary drags independently: 1:1, its own pair only, remembered on release
    print('\n-- layout: each boundary drags 1:1 and moves only its own pair')
    start = column_widths(app)
    drag(app, app.contents_sash, 40)
    pump(root, app, 0.6)
    moved = column_widths(app)
    check('the strip|pane sash widens the strip by the drag and narrows the pane by it',
          moved[0] == start[0] + 40 and moved[1] == start[1] - 40, (start, moved))
    check('and leaves the prompt and the picture exactly where they were', moved[2:] == start[2:], (start, moved))
    drag(app, app.gallery_sash, -50)
    pump(root, app, 0.6)
    gallery = column_widths(app)
    check('the gallery|prompt sash grows the prompt by the drag', gallery[2] == moved[2] + 50, (moved, gallery))
    check('and takes it from the gallery column, pane first', gallery[0] + gallery[1] == moved[0] + moved[1] - 50,
          (moved, gallery))
    check('while the picture column does not move', gallery[3] == moved[3], (moved, gallery))
    drag(app, app.prompt_sash, 30)
    pump(root, app, 0.6)
    prompt = column_widths(app)
    if gallery[3] <= zui.PICTURE_MIN_W:
        # At this window the picture already sits on its stated floor, so this boundary is at its clamp: a drag must move
        # nothing (the alternative would be clipping the picture column). Movement is then shown on a wider window.
        check('the prompt|picture sash is clamped: the picture is on its floor, so a drag moves nothing',
              prompt == gallery, (gallery, prompt))
        # A widget cannot be wider than the window it is in, so the wide case is asserted on the arithmetic the layout
        # computes for that width (`_layout_widths` is pure in the window width) rather than by faking a 2560 window.
        wide = app._layout_widths(2560)
        here = app._layout_widths()
        check('with a 2560 px window the picture column is off its floor, so this sash has room to move',
              wide[zui.COL_PICTURE] > zui.PICTURE_MIN_W, (wide[zui.COL_PICTURE], zui.PICTURE_MIN_W))
        check('and the extra width is what the pair shares', wide[zui.COL_PROMPT] + wide[zui.COL_PICTURE]
              > here[zui.COL_PROMPT] + here[zui.COL_PICTURE],
              (here[zui.COL_PROMPT] + here[zui.COL_PICTURE], wide[zui.COL_PROMPT] + wide[zui.COL_PICTURE]))
        app._apply_layout()
        pump(root, app, 0.4)
    else:
        check('the prompt|picture sash grows the prompt by the drag and shrinks the picture by it',
              prompt[2] == gallery[2] + 30 and prompt[3] == gallery[3] - 30, (gallery, prompt))
        check('and the two on its left do not move', prompt[:2] == gallery[:2], (gallery, prompt))

    # clamps at both ends, keys and defaults
    print('\n-- layout: every boundary clamps at both ends, and Home/End/keys/double-click obey')
    drag(app, app.contents_sash, 10 ** 4)
    pump(root, app, 0.5)
    check('strip|pane stops with the pane at its stated minimum',
          app._contents_w == zui.CONTENTS_W_MIN, (app._contents_w, zui.CONTENTS_W_MIN))
    check('and the strip is exactly the rest of the gallery column',
          app._strip_w + app._contents_w == app._gallery_col_w() - (zui.SASH_W + zui.PANE_PAD + zui.GAL_PAD
                                                                    + app._bar_px(app.contents_bar)),
          (app._strip_w, app._contents_w, app._gallery_col_w()))
    drag(app, app.contents_sash, -10 ** 4)
    pump(root, app, 0.5)
    check('the other end is the pane\'s widest, which never takes the strip below its floor',
          app._contents_w == app._contents_w_limit() and app._strip_w >= zui.GAL_FLOOR_W,
          (app._contents_w, app._contents_w_limit(), app._strip_w))
    app._sash_jump('strip', 'max')                  # End: the pane to its narrowest, so the keys have room to move
    pump(root, app, 0.3)
    was = app._contents_w
    app._sash_step('strip', 1, False)               # Left: the strip shrinks, the pane grows 16
    pump(root, app, 0.3)
    left = app._contents_w
    app._sash_step('strip', -1, False)              # Right: 16 back
    pump(root, app, 0.3)
    check('Left/Right move the strip|pane boundary 16 px each way', (was, left, app._contents_w)
          == (zui.CONTENTS_W_MIN, zui.CONTENTS_W_MIN + 16, zui.CONTENTS_W_MIN), (was, left, app._contents_w))
    check('and a key press persists at once, with no release to wait for',
          zui.load_settings().get('contents_w') == app._contents_w,
          (zui.load_settings().get('contents_w'), app._contents_w))
    app._sash_step('strip', 1, True)                # Shift+Left: 64
    pump(root, app, 0.3)
    check('Shift moves it 64, or to the end of its range when that is nearer',
          app._contents_w == min(app._contents_w_limit(), zui.CONTENTS_W_MIN + 64),
          (app._contents_w, min(app._contents_w_limit(), zui.CONTENTS_W_MIN + 64)))
    drag(app, app.gallery_sash, 10 ** 4)
    pump(root, app, 0.5)
    check('gallery|prompt stops at the prompt\'s own measured floor',
          app._prompt_w == app._prompt_floor(), (app._prompt_w, app._prompt_floor()))
    check('which is what its control rows need, so nothing in it is clipped',
          app.text.master.winfo_width() >= app._prompt_floor(), (app.text.master.winfo_width(), app._prompt_floor()))
    drag(app, app.gallery_sash, -10 ** 4)
    pump(root, app, 0.5)
    strip_floor = app._column_floors()[zui.COL_STRIP]
    check('and at the other end the gallery column is its two floors and the prompt has the rest',
          app._contents_w == zui.CONTENTS_W_MIN and app._strip_w == strip_floor
          and app._prompt_w > app._prompt_floor(),
          (app._contents_w, app._strip_w, strip_floor, app._prompt_w, app._prompt_floor()))
    drag(app, app.prompt_sash, 10 ** 4)
    pump(root, app, 0.5)
    check('prompt|picture stops at the picture\'s floor',
          app._layout_widths()[zui.COL_PICTURE] >= zui.PICTURE_MIN_W, app._layout_widths())
    app._sash_default('gallery')
    pump(root, app, 0.5)
    check('a double-click puts that boundary back to the window\'s even split',
          app._strip_w == app._even_widths()[zui.COL_STRIP], (app._strip_w, app._even_widths()))
    saved = zui.load_settings()
    check('every boundary writes its width to the settings file',
          all(isinstance(saved.get(k), int) for k in zui.LAYOUT_KEYS),
          {k: saved.get(k) for k in zui.LAYOUT_KEYS})

    # restoring: what a start reads out of that file, and what it does with a window that no longer fits
    reread = zui.load_settings()
    check('a fresh start reads the same three boundaries back',
          tuple(zui._px_setting(reread, k) for k in zui.LAYOUT_KEYS)
          == (app._strip_w, app._contents_w, app._prompt_w),
          ([zui._px_setting(reread, k) for k in zui.LAYOUT_KEYS],
           (app._strip_w, app._contents_w, app._prompt_w)))
    check('so a window that opens on that file starts with a layout the reader set',
          all(zui._px_setting(reread, k) for k in zui.LAYOUT_KEYS),
          {k: zui._px_setting(reread, k) for k in zui.LAYOUT_KEYS})
    remembered = column_widths(app)
    clamped = app._layout_widths(1200)            # a window far narrower than the layout: re-clamped, not overflowed
    check('and a narrower window re-clamps them instead of overflowing',
          sum(clamped.values()) <= app._content_w(1200), (clamped, app._content_w(1200)))
    check('taking the difference out of the strip first, which is the column that reflows',
          clamped[zui.COL_STRIP] <= remembered[0] and clamped[zui.COL_PANE] == remembered[1]
          and clamped[zui.COL_PROMPT] == remembered[2], (remembered, clamped))
    # a profile that never had a boundary dragged opens on the even split instead, whatever it was left at
    app._layout_user = False
    app._apply_layout()
    pump(root, app, 0.5)
    check('an untouched profile opens on the even split, not on the last window\'s widths',
          column_widths(app) == tuple(app._even_widths()[c] for c in zui.COLUMNS),
          (column_widths(app), app._even_widths()))

    # the pane is present with no folder open, and no redraw changes its width (idempotence, like L1)
    print('\n-- layout: the folder pane is never collapsed, so no redraw re-lays anything')
    app._layout_user = False
    app._apply_layout()
    pump(root, app, 0.6)
    at_start = column_widths(app)
    check('with no folder open the pane is still gridded', app.contents_holder.winfo_manager() == 'grid',
          app.contents_holder.winfo_manager())
    check('and holds its empty state rather than nothing', app._pane_empty
          and len(app.contents.winfo_children()) == 1,
          (app._pane_empty, len(app.contents.winfo_children())))
    for _ in range(3):
        app.refresh_gallery()
        pump(root, app, 0.5)
        app._regroup_soon()
        pump(root, app, 2.2)
        for _ in range(40):
            app._poll()
            root.update()
            time.sleep(0.03)
        check('a refresh and a re-group leave the four columns exactly where they were',
              column_widths(app) == at_start, (at_start, column_widths(app)))
        check('and the pane is the same width it was', app.contents_canvas.winfo_width() == at_start[1],
              (app.contents_canvas.winfo_width(), at_start[1]))

    # a drag is a burst of re-layouts: count them, the way L2 counts the strip's
    print('\n-- layout: a synthetic drag queues a bounded number of re-layouts')
    counts = {'refresh': 0, 'tiles': 0, 'resize': 0, 'layout': 0}
    real_refresh, real_tiles, real_resize = (zui.App.refresh_gallery, zui.App._render_folder_tiles,
                                             zui.App._apply_strip_resize)
    real_layout = zui.App._apply_layout

    def count(name, real):
        def wrapped(self, *a, **k):
            counts[name] += 1
            return real(self, *a, **k)
        return wrapped

    zui.App.refresh_gallery = count('refresh', real_refresh)
    zui.App._render_folder_tiles = count('tiles', real_tiles)
    zui.App._apply_strip_resize = count('resize', real_resize)
    zui.App._apply_layout = count('layout', real_layout)
    try:
        drag(app, app.contents_sash, -120, steps=40)
        pump(root, app, 1.2)
        total = counts['refresh'] + counts['tiles']
        check('a 40-step drag on one boundary re-lays the strip out at most twice', total <= 2, dict(counts))
        check('and never re-renders the folder grid', counts['tiles'] <= 1, dict(counts))
        check('the layout is applied once per motion plus once per commit - not once per pixel event',
              counts['layout'] <= 41, dict(counts))
    finally:
        zui.App.refresh_gallery, zui.App._render_folder_tiles, zui.App._apply_strip_resize = (
            real_refresh, real_tiles, real_resize)
        zui.App._apply_layout = real_layout
    app._layout_user = False
    app._apply_layout()
    pump(root, app, 0.6)
    # ---- B1: the strip is nine pictures at three across, from the canvas it has
    print('\n-- B1: the strip fits 3 across x 3 down without clipping')
    canvas = app.gal_canvas
    cw, ch = canvas.winfo_width(), canvas.winfo_height()
    cols, tile = app._strip_metrics()
    check('three columns', cols == zui.STRIP_FIT_COLS == 3, (cols, cw))
    check('the tile is the width three of them fill', tile == cw // cols - zui.TILE_CHROME, (tile, cw))
    check('at least the 160 px fit size', tile >= zui.STRIP_TILE_FIT, tile)
    check('inside the 320 px cap', tile <= zui.STRIP_TILE_MAX, tile)
    check('the height gives the three rows the same size, so neither dimension clips',
          tile <= ch // zui.STRIP_FIT_ROWS - zui.TILE_CHROME, (tile, ch))
    shown = cells(app)
    nine = strip_rows(shown)[:zui.STRIP_FIT_COLS * zui.STRIP_FIT_ROWS]
    check('nine tiles are laid out', len(nine) >= 9, (len(shown), len(nine)))
    check('the first nine do not run under the scrollbar', right_edge(nine) <= cw,
          (right_edge(nine), cw))
    check('nor under the bottom of the viewport', bottom_edge(nine) <= ch, (bottom_edge(nine), ch))
    check('every tile is the size the metrics promised',
          all(w.thumb_px == tile for w in nine), sorted({w.thumb_px for w in nine}))
    check('and carries only its own chrome around that picture',
          all(w.winfo_width() == w.thumb_px + zui.TILE_CHROME - 2 for w in nine),
          sorted({(w.winfo_width(), w.thumb_px) for w in nine}))
    check('three across is a wider tile than four would have been in this canvas',
          tile > canvas.winfo_width() // 4 - zui.TILE_CHROME, (tile, cw))

    # a narrower canvas must give up a column rather than clip the last one
    print('\n-- B1: a narrow canvas gives up a column, not the edge of the last one')
    root.state('normal')
    root.geometry('1100x700+20+20')     # below the window's own minimum, so the window clamps to it
    pump(root, app, 0.9)
    cw2 = canvas.winfo_width()
    cols2, tile2 = app._strip_metrics()
    narrow = cells(app)
    check('the metrics fit the width they are given', cols2 * (tile2 + zui.TILE_CHROME) <= cw2,
          (cols2, tile2, cw2))
    check('and the tiles it laid out agree', right_edge(narrow) <= cw2, (right_edge(narrow), cw2))
    check('a narrow column never goes below the 96 px floor', tile2 >= zui.STRIP_TILE_MIN, tile2)
    check('the smallest window still shows the nine',
          len([w for w in narrow if int(w.grid_info()['row']) < zui.STRIP_FIT_ROWS]) >= 9,
          (len(narrow), cw2))
    root.state('zoomed')
    pump(root, app, 0.9)

    # ---- B3: the pane reserves its scrollbar and reflows instead of drawing past its edge
    print('\n-- B3: the open-folder pane keeps its pictures inside the canvas')
    app.toggle_groups(True)
    pump(root, app, 1.6)
    for _ in range(80):
        app._poll()
        root.update()
        if app._plan and app._plan.get('groups'):
            break
        time.sleep(0.05)
    app._content_shown = 200                     # force enough rows for the pane's scrollbar to appear
    app.select_folder(0)
    pump(root, app, 1.2)
    pane = app.contents_canvas
    bar = app.contents_bar
    pcols, ptile = app._contents_metrics(pane.winfo_width())
    pane_cells = cells(app, app.contents)
    check('the pane laid out three per row', pcols == 3, (pcols, pane.winfo_width()))
    check('its cells sit inside the canvas', right_edge(pane_cells) <= pane.winfo_width(),
          (right_edge(pane_cells), pane.winfo_width()))
    check('the scrollbar is a real width, queried from the widget',
          zui.App._bar_px(bar) == bar.winfo_reqwidth() and zui.App._bar_px(bar) > 8,
          zui.App._bar_px(bar))
    check('the canvas is exactly the width the sash promised it, so the pictures keep their column',
          pane.winfo_width() == app._contents_w, (pane.winfo_width(), app._contents_w))
    check('and the bar has its own column beside it rather than a slice of the canvas',
          bar.winfo_width() >= 8 and bar.winfo_rootx() >= pane.winfo_rootx() + pane.winfo_width(),
          (bar.winfo_width(), bar.winfo_rootx(), pane.winfo_rootx() + pane.winfo_width()))
    # a pane narrowed by the window must re-lay out, not leave its last column outside the canvas
    root.geometry('1300x760+20+20')
    pump(root, app, 1.0)
    pane_cells = cells(app, app.contents)
    check('a resized pane reflows inside its new width',
          right_edge(pane_cells) <= app.contents_canvas.winfo_width(),
          (right_edge(pane_cells), app.contents_canvas.winfo_width()))
    # The pane is a column of the layout now (2026-10-30), not a view that comes and goes: opening nothing must leave
    # it gridded, at the width it had, with its empty state inside it - the collapse and re-grow was one of the redraws
    # the report was about, because the strip re-lays itself out when its width moves.
    open_canvas = app.gal_canvas.winfo_width()
    pane_w = app.contents_canvas.winfo_width()
    app.select_folder(None)
    pump(root, app, 0.6)
    check('closing the folder leaves the pane gridded, not collapsed',
          app.contents_holder.winfo_manager() == 'grid' and app._pane_shown,
          (app.contents_holder.winfo_manager(), app._pane_shown))
    check('with its empty state in it, not a blank column',
          app._pane_empty and len(app.contents.winfo_children()) == 1,
          (app._pane_empty, len(app.contents.winfo_children())))
    check('and the strip did not gain the width back, so it has nothing to re-lay out',
          app.gal_canvas.winfo_width() == open_canvas
          and app.contents_canvas.winfo_width() == pane_w,
          (open_canvas, app.gal_canvas.winfo_width(), pane_w, app.contents_canvas.winfo_width()))
    root.state('zoomed')
    pump(root, app, 0.6)

    # ---- B2: the finish screen is the same 4x3 grid, larger, and scrolls
    print('\n-- B2: "Ranked by wins" is twelve at a time, larger than the strip, and scrolls beyond')
    win = tk.Toplevel(root)
    win.title('sizing finish')
    win.geometry('1600x900+0+0')
    win.columnconfigure(0, weight=1)
    win.columnconfigure(1, weight=1)
    win.rowconfigure(0, weight=1)
    left, right = tk.Label(win), tk.Label(win)
    left.grid(row=0, column=0, sticky='nsew')
    right.grid(row=0, column=1, sticky='nsew')
    win.update()
    state = {'paths': [str(p) for p in paths], 'pairs': [], 'deleted': []}
    shell = {'win': win, 'state': state, 'left_lbl': left, 'right_lbl': right, 'buttons': (), 'gallery_buttons': (),
             'badge': None, 'deleted': 0, 'queue': None, 'path': None, 'cur': 0}
    app._review = shell
    app._review_finish(shell, decided=6, total=19, deletes=0)
    for _ in range(60):
        win.update()
        app._poll()
        time.sleep(0.02)
        if shell.get('finish_geom'):
            break
    tiles = shell.get('finish_tiles') or []
    size = shell.get('finish_size') or 0
    fcols, ftile = shell.get('finish_geom') or (0, 0)
    check('the grid is four across', fcols == zui.REVIEW_FINISH_COLS, fcols)
    check('every ranked picture has a slot', len(tiles) >= 12 and len(tiles) == len(state['paths']),
          (len(tiles), len(state['paths'])))
    check('the twelve slots of the first page are laid out in three rows',
          len([t for t in tiles if t.winfo_y() + t.winfo_height() <= shell['finish_canvas'].winfo_height()]) >= 12,
          [t.winfo_y() for t in tiles[:12]])
    strip_tile = app._strip_metrics()[1]
    check('the finish tile is larger than a strip tile', size > strip_tile, (size, strip_tile))
    check('and none of them is a thumbnail', size >= zui.REVIEW_TILE_MIN, size)
    check('the grid is a scroll target past twelve', shell['finish_canvas'].yview()[1] < 1.0,
          shell['finish_canvas'].yview())
    check('the tiles fit the canvas they were sized for',
          max(t.winfo_x() + t.winfo_width() for t in tiles) <= shell['finish_canvas'].winfo_width(),
          (max(t.winfo_x() + t.winfo_width() for t in tiles), shell['finish_canvas'].winfo_width()))
    win.destroy()
    app._review = None

    # ---- B4: the lists follow the sort mode
    print('\n-- B4: Wins orders the list by wins, not by favourites and date')
    a, b, c = paths[0], paths[1], paths[2]
    pair_rank.record({}, str(c), str(a), 'left_wins')
    pair_rank.record({}, str(c), str(b), 'left_wins')
    app._wins_cache.clear()
    app.sort_var.set('Wins')
    app.refresh_gallery()
    order = [p.name for p in app._visible_files()]
    check('a two-win picture comes first', order[0] == c.name, order[:3])
    check('the two pictures it beat come last, fewest losses first and newest of them first',
          order[-2:] == [b.name, a.name], order[-3:])
    check('the sorted list is what the strip reads', [p.name for p in app._files][:3] == order[:3],
          [p.name for p in app._files][:3])

    print('\n-- B4: Sequence is film order, Recent still favourites-first by date')
    for i, p in enumerate((a, b, c)):
        meta = pair_rank.read_meta(p)
        meta['scene'] = 7
        meta['scene_of'] = 3 - i          # so the oldest file is the last picture in the scene
        pair_rank.write_meta(p, meta)
    app.sort_var.set('Sequence')
    app.refresh_gallery()
    seq = [p.name for p in app._visible_files()][:3]
    check('Sequence puts the numbered pictures in film order, scene position first',
          seq == [c.name, b.name, a.name], seq)
    app.sort_var.set('Recent')
    app.refresh_gallery()
    meta = pair_rank.read_meta(b)
    meta['star'] = True
    pair_rank.write_meta(b, meta)
    app._favorites = {b.name}
    app.refresh_gallery()
    recent = [p.name for p in app._visible_files()]
    check('Recent puts a favourite first', recent[0] == b.name, recent[:3])

    check('the strip still takes the keyboard', bool(app.gal_canvas.cget('takefocus')), app.gal_canvas.cget('takefocus'))

    # ---- B5: a tile that lands mid-run is in the list, and a small folder fills its tile
    print('\n-- B5: a tile that lands mid-run appears in the list with folders on')
    app.toggle_groups(True)
    pump(root, app, 1.6)
    for _ in range(100):
        app._poll()
        root.update()
        if app._plan and app._plan.get('groups'):
            break
        time.sleep(0.05)
    app.busy = True                      # an n-up run in flight: the strip follows the tiles as they land
    fresh = tmp / 'gallery' / '20261030-140000_512x512_seed999.png'
    Image.new('RGB', (320, 240), (10, 200, 40)).save(fresh)
    fresh.with_suffix('.json').write_text(json.dumps({'prompt': 'a cat number 9', 'seed': 999}), encoding='utf-8')
    os.utime(fresh, (time.time(), time.time()))
    before = len(app._files)
    app.engine.events.put(('tile', (fresh.read_bytes(), str(fresh), 1, 4)))
    pump(root, app, 0.8)
    check('the file list grew at once', len(app._files) == before + 1, (before, len(app._files)))
    check('and the new tile is painted in the strip',
          any(Path(p).name == fresh.name for p in app._tile_widgets), len(app._tile_widgets))
    app.busy = False
    app.refresh_gallery()
    pump(root, app, 0.5)
    check('the folders come back when the run ends', app._folders_view_active(), app.gal_title['text'])

    print('\n-- B5: a folder with fewer than four pictures fills its tile')
    for n in (1, 2, 3, 4):
        boxes = zui.App._sheet_cells(200, n)
        check('%d picture(s) -> %d cells' % (n, n), len(boxes) == n, boxes)
        check('  and they cover the whole tile', sum(w * h for _x, _y, w, h in boxes) == 200 * 200,
              sum(w * h for _x, _y, w, h in boxes))
    bg = app.theme.sheet_bg
    bg_rgb = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
    probe = [{'path': str(paths[0])}]
    sheet = app._folder_sheet({'label': 'one picture', 'items': probe}, 160)
    px = sheet.load()
    filled = [(80, 30), (80, 80), (80, 130)]
    check('one picture is drawn across the tile, not into one quarter',
          all(tuple(px[p][:3]) != bg_rgb for p in filled),
          [tuple(px[p][:3]) for p in filled])
    print('\n-- B6: the strip does not oscillate while tiles land')
    app.toggle_groups(False)
    pump(root, app, 0.6)
    seen = []
    for k in range(6):
        p = tmp / 'gallery' / ('20261030-1500%02d_512x512_seed%d.png' % (k, 700 + k))
        Image.new('RGB', (320, 240), (90 + 10 * k, 40, 120)).save(p)
        p.with_suffix('.json').write_text(json.dumps({'prompt': 'a cat number %d' % k, 'seed': 700 + k}),
                                         encoding='utf-8')
        os.utime(p, (time.time() + k, time.time() + k))
        app.engine.events.put(('tile', (p.read_bytes(), str(p), k + 1, 6)))
        pump(root, app, 0.5)
        cols, tile = app._strip_metrics()
        seen.append((app.gal_canvas.winfo_width(), cols, tile, sorted(app.gal_bar.state())))
    print('   (canvas, cols, tile, bar) per refresh: %s' % (seen,))
    check('the strip canvas keeps one width as tiles land', len({s[0] for s in seen}) == 1, seen)
    check('so the tile never changes size mid-refresh', len({s[2] for s in seen}) == 1, seen)
    box = app.gal_canvas.bbox('all') or (0, 0, 0, 0)
    over = (box[3] - box[1]) > app.gal_canvas.winfo_height() + 1
    check('the bar is enabled exactly when the list overflows',
          ('disabled' in app.gal_bar.state()) == (not over),
          (over, box, app.gal_canvas.winfo_height(), sorted(app.gal_bar.state())))
    check('the bar keeps its cell instead of appearing and disappearing',
          app.gal_bar.winfo_manager() == 'grid',
          (app.gal_bar.winfo_manager(), sorted(app.gal_bar.state())))

    # ---- L1: a redraw must not leave the folder tiles sized for a canvas they are no longer in
    print('\n-- L1: folder tiles are composed for the canvas they are drawn in, redrawn or not')
    app.toggle_groups(True)
    pump(root, app, 1.6)
    for _ in range(120):
        app._poll()
        root.update()
        if app._plan and app._plan.get('groups'):
            break
        time.sleep(0.05)
    pump(root, app, 0.6)
    app.select_folder(0)
    pump(root, app, 1.2)

    def laid_out():
        """Everything a second redraw must not move, plus the widths of the folder tiles it must not resize."""
        return (int(root.grid_columnconfigure(0)['minsize']), app.gal.winfo_width(),
                app.gal_canvas.winfo_width(), app.contents_canvas.winfo_width(),
                app.text.master.winfo_reqwidth(),
                tuple(sorted(w.winfo_width() for w in app._folder_tiles())))

    def tiles_fit():
        tiles = app._folder_tiles()
        return bool(tiles) and right_edge(tiles) <= app.gal_canvas.winfo_width() + 2

    settled = laid_out()
    check('with a folder open the folder tiles are inside the strip canvas', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width(), settled[5]))
    check('and the predicate agrees with the tiles that are on screen',
          bool(app._folders_view_active()) == bool(app._folder_tiles()),
          (app._folders_view_active(), len(app._folder_tiles())))
    app.refresh_gallery()
    pump(root, app, 0.9)
    check('a redraw leaves the layout where it was', laid_out() == settled, (laid_out(), settled))
    app._regroup_soon()
    pump(root, app, 3.5)
    for _ in range(80):
        app._poll()
        root.update()
        time.sleep(0.05)
    check('and so does the re-group that follows a render', laid_out() == settled, (laid_out(), settled))
    # the widths move: a pane that closes, and a window that narrows, must re-lay the tiles out - the "folders doubled
    # in size and the right column is cut off" report was tiles composed at 337 px still sitting in a 343 px canvas.
    # The pane no longer closes (2026-10-30), so the width that moves now is the one a sash moves.
    app.select_folder(None)
    pump(root, app, 1.0)
    check('closing the folder re-lays nothing, because no width moved', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width()))
    app.select_folder(0)
    pump(root, app, 1.0)
    check('and opening it again re-lays nothing either', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width()))
    closed = (app.gal_canvas.winfo_width(), sorted(w.winfo_width() for w in app._folder_tiles()))
    app.select_folder(None)
    pump(root, app, 0.8)
    app.select_folder(0)
    pump(root, app, 0.8)
    check('so the folder grid is the same grid after a click on nothing and a click back',
          (app.gal_canvas.winfo_width(), sorted(w.winfo_width() for w in app._folder_tiles())) == closed,
          (closed, app.gal_canvas.winfo_width(), sorted(w.winfo_width() for w in app._folder_tiles())))
    root.state('normal')
    root.geometry('1200x700+20+20')
    pump(root, app, 1.4)
    check('a narrower window re-lays them out', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width()))
    root.state('zoomed')
    pump(root, app, 1.4)
    check('and so does a wider one', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width()))
    # three tiles per row rather than two, from the column they are in (2026-10-30 follow-up)
    width, fcols, sheet = app._folder_geom_now()
    tiles = app._folder_tiles()
    check('the folder grid is three across where the column can hold them',
          fcols == zui.FOLDER_COLS == 3, (fcols, width))
    check('and the tiles on screen are laid out in that many columns',
          len({int(w.grid_info()['column']) for w in tiles}) == min(3, len(tiles)),
          [int(w.grid_info()['column']) for w in tiles])
    check('a folder tile is the width of its column, not a constant',
          sheet == max(zui.STRIP_TILE_MIN, (width - zui.FOLDER_PAD * fcols) // fcols), (sheet, width))
    check('which makes it smaller than the 337 px cell the old two-up grid produced here',
          sheet < 337, sheet)
    # the tiles follow the column: a sash drag re-lays the grid out from the canvas it ends at
    app._set_contents_w(zui.CONTENTS_W_MIN, persist=False)
    pump(root, app, 1.2)
    narrow_w, narrow_cols, narrow_sheet = app._folder_geom_now()
    narrow_tiles = app._folder_tiles()
    check('a pane dragged narrower re-lays the folder grid out for the canvas it ends up with',
          narrow_w != width and right_edge(narrow_tiles) <= app.gal_canvas.winfo_width() + 2,
          (width, narrow_w, right_edge(narrow_tiles), app.gal_canvas.winfo_width()))
    check('and the tiles it re-laid are the size that canvas says',
          narrow_sheet == max(zui.STRIP_TILE_MIN,
                              (narrow_w - zui.FOLDER_PAD * narrow_cols) // narrow_cols)
          and all(w.winfo_width() <= narrow_sheet + zui.FOLDER_PAD for w in narrow_tiles),
          (narrow_sheet, narrow_cols, narrow_w, sorted({w.winfo_width() for w in narrow_tiles})))
    app._set_contents_w(300, persist=False)
    pump(root, app, 0.8)

    # ---- L2: the same boundary through its *bindings*, which is what a real pointer drives
    print('\n-- splitter: the bindings move the boundary, and a drag never re-lays the strip out')
    app.select_folder(0)
    pump(root, app, 1.0)
    app._layout_user = False
    app._apply_layout()
    pump(root, app, 0.5)
    sash = app.contents_sash
    check('the sash is grid-managed between the strip canvas and the pane canvas',
          sash.winfo_manager() == 'grid'
          and app.gal_canvas.winfo_rootx() + app.gal_canvas.winfo_width() <= sash.winfo_rootx()
          and sash.winfo_rootx() + sash.winfo_width() <= app.contents_canvas.winfo_rootx(),
          (app.gal_canvas.winfo_rootx() + app.gal_canvas.winfo_width(), sash.winfo_rootx(),
           app.contents_canvas.winfo_rootx()))
    check('its hit area is 6-8 px wide', 6 <= sash.winfo_width() <= 8, sash.winfo_width())
    check('it carries the resize cursor', sash.cget('cursor') == 'sb_h_double_arrow', sash.cget('cursor'))
    check('it is a tab stop with a visible focus ring',
          bool(sash.cget('takefocus')) and int(sash.cget('highlightthickness')) > 0
          and str(sash.cget('highlightcolor')) == app.theme.accent,
          (sash.cget('takefocus'), sash.cget('highlightthickness'), sash.cget('highlightcolor')))
    # a focused sash wears the accent by design, so "at rest" means the keyboard is somewhere else
    app.text.focus_set()
    root.update()
    check('the grip is a 2 px line, quiet at rest',
          app.sash_grip.winfo_width() == 2 and str(app.sash_grip.cget('background')) == app.theme.border,
          (app.sash_grip.winfo_width(), app.sash_grip.cget('background')))
    sash.event_generate('<Enter>')
    root.update()
    hovered = str(app.sash_grip.cget('background'))
    sash.event_generate('<Leave>')
    root.update()
    check('hovering tints it and leaving puts it back',
          hovered == app.theme.accent and str(app.sash_grip.cget('background')) == app.theme.border,
          (hovered, app.sash_grip.cget('background')))
    # one event-driven drag: the arithmetic below is driven through the handlers, so this is what proves the widget is
    # wired to them (a second synthetic drag in the same run loses its motions to a grab handover - measured)
    pane_before = app.contents_canvas.winfo_width()
    x, y = sash.winfo_rootx() + sash.winfo_width() // 2, sash.winfo_rooty() + 20
    sash.event_generate('<Button-1>', x=2, y=20, rootx=x, rooty=y)
    root.update()
    sash.event_generate('<B1-Motion>', x=2, y=20, rootx=x + 40, rooty=y)
    root.update()
    check('a press and a motion through the binding move the boundary 1:1',
          app._contents_w == app._even_widths()[zui.COL_PANE] - 40, app._contents_w)
    sash.event_generate('<ButtonRelease-1>', x=2, y=20, rootx=x + 40, rooty=y)
    pump(root, app, 0.9)
    check('and the pane canvas follows it',
          abs(app.contents_canvas.winfo_width() - (pane_before - 40)) <= 3,
          (app.contents_canvas.winfo_width(), pane_before))
    check('with the width written to the settings file on the release',
          zui.load_settings().get('contents_w') == app._contents_w,
          (zui.load_settings().get('contents_w'), app._contents_w))
    check('the strip keeps its full row of columns at the narrow end of the pane',
          app._strip_metrics()[0] == zui.STRIP_FIT_COLS, app._strip_metrics())
    # a drag is a burst of <Configure> events: count what it queues
    counts = {'refresh': 0, 'tiles': 0, 'resize': 0}
    real_refresh, real_tiles, real_resize = (zui.App.refresh_gallery, zui.App._render_folder_tiles,
                                             zui.App._apply_strip_resize)

    def count_refresh(self, *a, **k):
        counts['refresh'] += 1
        return real_refresh(self, *a, **k)

    def count_tiles(self, *a, **k):
        counts['tiles'] += 1
        return real_tiles(self, *a, **k)

    def count_resize(self, *a, **k):
        counts['resize'] += 1
        return real_resize(self, *a, **k)

    zui.App.refresh_gallery, zui.App._render_folder_tiles, zui.App._apply_strip_resize = (
        count_refresh, count_tiles, count_resize)
    try:
        drag(app, app.contents_sash, -40, steps=40)
        pump(root, app, 1.4)
        total = counts['refresh'] + counts['tiles']
        check('a 40-step drag queues a bounded number of rebuilds', total <= 4, dict(counts))
        check('and no burst of them while the pointer is down', counts['resize'] <= 4, dict(counts))
        check('the strip bar keeps its grid cell through the drag',
              app.gal_bar.winfo_manager() == 'grid', app.gal_bar.winfo_manager())
    finally:
        zui.App.refresh_gallery, zui.App._render_folder_tiles, zui.App._apply_strip_resize = (
            real_refresh, real_tiles, real_resize)
    app.select_folder(None)
    pump(root, app, 0.6)
    app._layout_user = False
    app._apply_layout()
    pump(root, app, 0.5)

    # ---- zoom: 100 % is the picture's own pixels, and a bigger picture pans inside its own bounds
    print('\n-- zoom: 100 % draws the picture at its own pixels')
    zoomdir = tmp / 'zoom'
    zoomdir.mkdir(parents=True, exist_ok=True)
    big = zoomdir / '20261030-160000_2400x1600_seed501.png'
    Image.new('RGB', (2400, 1600), (180, 60, 60)).save(big)
    small = zoomdir / '20261030-160001_200x150_seed502.png'
    Image.new('RGB', (200, 150), (60, 180, 60)).save(small)
    app._set_zoom(zui.ZOOM_DEFAULT)
    check('the level is a toolbar setting with a default of 100 %',
          app.zoom_var.get() == zui.ZOOM_DEFAULT == '100%', app.zoom_var.get())
    check('and it is listed the way the ask spells it',
          zui.ZOOM_STEPS == ('Fit', '50%', '75%', '100%', '125%', '150%', '200%', '400%'), zui.ZOOM_STEPS)

    app.select_file(small)
    pump(root, app, 0.4)
    check('100 % draws a picture that fits at the source size', app._draw[2:] == app._img.size,
          (app._draw[2:], app._img.size))
    check('and the canvas holds exactly those pixels, not a resample',
          (app._tk.width(), app._tk.height()) == app._img.size,
          (app._tk.width(), app._tk.height(), app._img.size))

    app.select_file(big)
    pump(root, app, 0.4)
    cw, ch = app.canvas.winfo_width(), app.canvas.winfo_height()
    check('100 % of a big picture is still 1:1 - the drawn size is the source size',
          app._draw[2:] == app._img.size == (2400, 1600), (app._draw[2:], app._img.size))
    check('so it is wider than the canvas it is drawn on', app._draw[2] > cw, (app._draw[2], cw))

    print('\n-- zoom: 400 % pans, and stops at the picture\'s own edge')
    app._set_zoom('400%')
    pump(root, app, 0.3)
    x0, y0, w, h = app._draw
    check('400 % is four times the source, exactly', (w, h) == (app._img.size[0] * 4, app._img.size[1] * 4),
          (w, h, app._img.size))
    check('it overflows the canvas both ways', w > cw and h > ch, (w, cw, h, ch))
    check('a zoomed picture keeps the corner it was pinned at - its top-right, flush, not off the canvas',
          (x0, y0) == (cw - w, 0), ((x0, y0), (cw - w, 0)))
    cx, cy = cw // 2, ch // 2

    app._on_canvas_click(NS(x=cx, y=cy))
    app._on_pan_drag(NS(x=cx + 99999, y=cy + 99999))
    app._on_pan_release(NS(x=cx + 99999, y=cy + 99999))
    check('dragging right and down stops with the picture\'s left/top edges at the canvas edges',
          app._draw[:2] == (0, 0), app._draw[:2])
    check('and it still covers the canvas - no blank strip beside it',
          app._draw[0] + w >= cw and app._draw[1] + h >= ch, (app._draw, cw, ch))
    app._on_canvas_click(NS(x=cx, y=cy))
    app._on_pan_drag(NS(x=cx - 99999, y=cy - 99999))
    app._on_pan_release(NS(x=cx - 99999, y=cy - 99999))
    check('the other way stops with the right/bottom edges at the canvas edges',
          app._draw[:2] == (cw - w, ch - h), ((app._draw[:2]), (cw - w, ch - h)))

    dx, dy = (w - cw) // 2, (h - ch) // 2
    app._on_canvas_click(NS(x=cx, y=cy))
    app._on_pan_drag(NS(x=cx + dx, y=cy + dy))
    check('inside the bounds a drag moves the picture by exactly the drag',
          app._draw[:2] == (cw - w + dx, ch - h + dy), (app._draw[:2], (cw - w + dx, ch - h + dy)))
    app._on_pan_drag(NS(x=cx, y=cy))
    check('and bringing the pointer back brings the picture back',
          app._draw[:2] == (cw - w, ch - h), (app._draw[:2], (cw - w, ch - h)))

    app._on_pan_drag(NS(x=cx + dx, y=cy + dy))          # back to the middle of the picture
    app._on_pan_release(NS(x=cx + dx, y=cy + dy))
    xm, ym = app._draw[:2]
    app._on_canvas_wheel(NS(delta=120, state=0))
    check('the wheel scrolls it vertically', (app._draw[0], app._draw[1]) == (xm, ym + zui.ZOOM_STEP),
          (app._draw[:2], (xm, ym + zui.ZOOM_STEP)))
    app._on_canvas_wheel(NS(delta=120, state=0x0001))
    check('Shift turns the same wheel horizontal',
          (app._draw[0], app._draw[1]) == (xm + zui.ZOOM_STEP, ym + zui.ZOOM_STEP),
          (app._draw[:2], (xm + zui.ZOOM_STEP, ym + zui.ZOOM_STEP)))
    app._on_pan_key(NS(keysym='Up', state=0))
    check('an arrow key pans by the same step', app._draw[1] == ym + 2 * zui.ZOOM_STEP,
          (app._draw[1], ym + 2 * zui.ZOOM_STEP))
    check('the canvas asks for the keyboard those keys need', bool(int(app.canvas.cget('takefocus'))),
          app.canvas.cget('takefocus'))

    print('\n-- zoom: Fit is today\'s behaviour - shrink a big picture, never enlarge a small one')
    app._set_zoom('Fit')
    pump(root, app, 0.3)
    fw, fh = app._draw[2:]
    check('Fit shrinks a picture bigger than the canvas', fw <= cw and fh <= ch and fw < 2400, (fw, fh, cw, ch))
    check('and keeps its proportions', abs(fw / float(fh) - 2400 / 1600.0) < 0.02, fw / float(fh))
    app.select_file(small)
    pump(root, app, 0.4)
    check('Fit leaves a picture smaller than the canvas alone', app._draw[2:] == (200, 150), app._draw[2:])

    print('\n-- zoom: the level is a view setting, so it outlives the picture')
    app._set_zoom('200%')
    pump(root, app, 0.2)
    saved = zui.load_settings()
    check('the level is saved beside the other view settings', saved.get('zoom') == '200%', saved.get('zoom'))
    app.select_file(big)
    pump(root, app, 0.4)
    check('and survives showing another picture', app.zoom_var.get() == '200%'
          and app._draw[2:] == (4800, 3200), (app.zoom_var.get(), app._draw[2:]))
    check('while the pan itself starts over at the pinned corner',
          app._draw[:2] == (cw - 4800, 0), (app._draw[:2], (cw - 4800, 0)))

    print('\n-- zoom: the pan never fights the three drags that already own this canvas')
    app._set_zoom('400%')
    pump(root, app, 0.3)
    x0, y0, w, h = app._draw

    check('a zoomed picture that overflows shows the pan cursor',
          str(app.canvas.cget('cursor')) == 'fleur', app.canvas.cget('cursor'))
    app._set_zoom('100%')
    app.select_file(small)                    # 200x150 at 100 %: it fits, so there is nothing to pan
    pump(root, app, 0.3)
    check('a picture that fits keeps the plain pointer', str(app.canvas.cget('cursor')) == '',
          (app.canvas.cget('cursor'), app._draw))
    app.select_file(big)
    app._set_zoom('400%')
    pump(root, app, 0.3)
    x0, y0, w, h = app._draw
    cx, cy = app.canvas.winfo_width() // 2, app.canvas.winfo_height() // 2

    app._pinned = Path(paths[0])
    app._base_img = None
    app.compare_var.set(True)
    app._base_img = None
    app._redraw()
    root.update()
    check('compare still draws over a zoomed picture',
          len(app.canvas.find_withtag('compare')) >= 4, len(app.canvas.find_withtag('compare')))
    check('and its drag does not wear the pan cursor', str(app.canvas.cget('cursor')) == '',
          app.canvas.cget('cursor'))
    before = app._draw
    app._on_canvas_click(NS(x=cx, y=cy))
    app._on_pan_drag(NS(x=cx + 300, y=cy + 300))
    check('a drag with compare on never pans', app._draw == before and app._pan_grab is None,
          (app._draw, before, app._pan_grab))
    app._split = 0.5
    app._on_compare_drag(NS(x=x0 + w // 4, y=y0 + h // 4))
    check('and the divider still follows the pointer', 0.15 < app._split < 0.35, app._split)
    app._split = 0.5
    app.compare_var.set(False)
    app._pinned = None
    app._redraw()
    root.update()

    app.mask_var.set(True)
    app.toggle_mask_mode(True)
    root.update()
    check('the mask is the picture\'s own size', app._mask is not None and app._mask.size == app._img.size,
          (app._mask.size if app._mask is not None else None, app._img.size))
    check('painting wears the pencil, not the pan arrow', str(app.canvas.cget('cursor')) == 'pencil',
          app.canvas.cget('cursor'))
    before = app._draw
    strokes = len(app.canvas.find_withtag('paint'))
    app._on_canvas_click(NS(x=cx, y=cy))
    app._on_pan_drag(NS(x=cx + 200, y=cy + 200))
    check('a drag with the mask armed paints, it does not pan',
          app._draw == before and app._pan_grab is None, (app._draw, before, app._pan_grab))
    check('and it left a stroke on the canvas', len(app.canvas.find_withtag('paint')) > strokes,
          (len(app.canvas.find_withtag('paint')), strokes))
    app.mask_var.set(False)
    app.toggle_mask_mode(False)

    app.crop_var.set(True)
    app.toggle_crop_mode(True)
    root.update()
    check('the crop rectangle wears the crosshair, not the pan arrow',
          str(app.canvas.cget('cursor')) == 'crosshair', app.canvas.cget('cursor'))
    before = app._draw
    app._on_canvas_click(NS(x=cx, y=cy))
    check('crop arms its rectangle on the press', app._crop_start is not None, app._crop_start)
    app._on_pan_drag(NS(x=cx + 200, y=cy + 200))
    check('a drag with crop armed never pans', app._draw == before and app._pan_grab is None,
          (app._draw, before, app._pan_grab))
    crop = app._crop_box(x0 + w // 4, y0 + h // 4, x0 + w // 2, y0 + h // 2)
    check('and the rectangle still maps onto the source at 400 %',
          crop is not None and abs((crop[2] - crop[0]) - app._img.size[0] / 4.0) <= 1,
          (crop, app._img.size))
    app.crop_var.set(False)
    app.toggle_crop_mode(False)

    # the bindings, not the handlers: the same synthetic drag has to separate by mode on its own
    app._pan_xy = None
    app._redraw()
    root.update()
    before = app._draw
    app.canvas.event_generate('<Button-1>', x=cx, y=cy)
    app.canvas.event_generate('<B1-Motion>', x=cx + 250, y=cy + 250)
    app.canvas.update()
    check('a real press-and-drag pans the picture', app._draw != before, (before, app._draw))
    app.canvas.event_generate('<ButtonRelease-1>', x=cx + 250, y=cy + 250)
    app.canvas.update()
    check('and the release ends the pan', app._pan_grab is None, app._pan_grab)
    app._pan_xy = None
    app.crop_var.set(True)
    app.toggle_crop_mode(True)
    root.update()
    before = app._draw
    app.canvas.event_generate('<Button-1>', x=cx, y=cy)
    app.canvas.event_generate('<B1-Motion>', x=cx + 250, y=cy + 250)
    app.canvas.update()
    check('the same real drag with crop armed arms the rectangle and leaves the picture alone',
          app._draw == before and app._crop_start is not None and app._pan_grab is None,
          (before, app._draw, app._crop_start, app._pan_grab))
    app.crop_var.set(False)
    app.toggle_crop_mode(False)
    app._set_zoom('100%')

    print('\n-- zoom: Alt+Z reaches the control, and a restart opens at the level it was left at')
    check('the zoom control is on the Alt access key', (app._access or {}).get('z') == 'zoom_btn',
          (app._access or {}).get('z'))
    check('the button underlines the key, the way the rest of the toolbar does',
          int(app.zoom_btn.cget('underline')) == 0
          and str(app.zoom_btn.cget('text'))[:1].lower() == 'z',
          (app.zoom_btn.cget('underline'), app.zoom_btn.cget('text')))
    check('and its tooltip names the key',
          any(t.widget is app.zoom_btn and 'Alt+Z' in t.text for t in app._tips),
          [t.text for t in app._tips if t.widget is app.zoom_btn])
    # Alt+Z posts this menu; posting is modal on Windows, so the suite never opens it - it drives the entries instead
    menu = app.zoom_menu
    check('the button owns that list', app.zoom_btn.cget('menu') == str(menu), (app.zoom_btn.cget('menu'), str(menu)))
    labels = [menu.entrycget(i, 'label') for i in range(len(zui.ZOOM_STEPS))]
    check('the list offers every level, in the order the ask gives them', tuple(labels) == zui.ZOOM_STEPS, labels)
    check('and nothing else', int(menu.tk.call(str(menu), 'index', 'end')) == len(zui.ZOOM_STEPS) - 1,
          menu.tk.call(str(menu), 'index', 'end'))
    check('the key opens that list', callable(getattr(app.zoom_btn, '_access_invoke', None)),
          getattr(app.zoom_btn, '_access_invoke', None))
    menu.invoke(1)                     # 50 %, straight from the menu the access key opens
    root.update()
    check('picking a level from it changes the zoom, and the button says so',
          app.zoom_var.get() == '50%' and app.zoom_btn.cget('text') == 'zoom 50%',
          (app.zoom_var.get(), app.zoom_btn.cget('text')))
    app._set_zoom('200%')
    pump(root, app, 0.2)
    root.destroy()

    root9 = tk.Tk()
    app9 = zui.App(root9, Stub(), args)
    root9.update()
    check('a new window opens at the level the last one was left at',
          app9.zoom_var.get() == '200%', app9.zoom_var.get())
    app9.select_file(big)
    for _ in range(30):
        root9.update()
        time.sleep(0.02)
        if app9._draw and app9._draw[2:] == (4800, 3200):
            break
    check('and draws the first picture it shows at it', app9._draw[2:] == (4800, 3200), app9._draw)
    root9.destroy()

    print('\n%d failure(s)' % len(FAILS))
    for f in FAILS:
        print('  FAILED: %s' % f)
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
