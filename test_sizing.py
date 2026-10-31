#!/usr/bin/env python
"""test_sizing.py - the four defects of 2026-10-30, measured rather than eyeballed.

    python test_sizing.py

Covers `docs/BUGS-20261030-tile-size.md`:

* B1 - the strip's tile is the size at which four across and three down fit its canvas, twelve visible;
* B2 - the finish screen is the same 4x3 grid, larger than a strip tile, and scrolls rather than truncating;
* B3 - the open-folder pane reserves its scrollbar and reflows instead of drawing past its edge;
* B4 - `Wins` and `Sequence` actually order the lists.

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
    """The first `REVIEW_FINISH_ROWS` rows of the strip: the twelve it promises before it scrolls."""
    return [w for w in widgets if int(w.grid_info()['row']) < zui.STRIP_FIT_ROWS]


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

    # ---- B1: the strip is twelve pictures at four across, from the canvas it has
    print('\n-- B1: the strip fits 4 across x 3 down without clipping')
    canvas = app.gal_canvas
    cw, ch = canvas.winfo_width(), canvas.winfo_height()
    cols, tile = app._strip_metrics()
    check('four columns', cols == zui.STRIP_FIT_COLS, (cols, cw))
    check('the tile is the width four of them fill', tile == cw // cols - zui.TILE_CHROME, (tile, cw))
    check('at least the 160 px fit size', tile >= zui.STRIP_TILE_FIT, tile)
    check('inside the 320 px cap', tile <= zui.STRIP_TILE_MAX, tile)
    shown = cells(app)
    twelve = strip_rows(shown)[:zui.STRIP_FIT_COLS * zui.STRIP_FIT_ROWS]
    check('twelve tiles are laid out', len(twelve) >= 12, (len(shown), len(twelve)))
    check('the first twelve do not run under the scrollbar', right_edge(twelve) <= cw,
          (right_edge(twelve), cw))
    check('nor under the bottom of the viewport', bottom_edge(twelve) <= ch, (bottom_edge(twelve), ch))
    check('every tile is the size the metrics promised',
          all(w.thumb_px == tile for w in twelve), sorted({w.thumb_px for w in twelve}))
    check('and carries only its own chrome around that picture',
          all(w.winfo_width() == w.thumb_px + zui.TILE_CHROME - 2 for w in twelve),
          sorted({(w.winfo_width(), w.thumb_px) for w in twelve}))

    # a narrower canvas must give up a column rather than clip the last one
    print('\n-- B1: a narrow canvas gives up a column, not the edge of the last one')
    root.state('normal')
    root.geometry('1100x700+20+20')     # below the 1200 px minimum, so the window clamps to it
    pump(root, app, 0.9)
    cw2 = canvas.winfo_width()
    cols2, tile2 = app._strip_metrics()
    narrow = cells(app)
    check('the metrics fit the width they are given', cols2 * (tile2 + zui.TILE_CHROME) <= cw2,
          (cols2, tile2, cw2))
    check('and the tiles it laid out agree', right_edge(narrow) <= cw2, (right_edge(narrow), cw2))
    check('a narrow column never goes below the 96 px floor', tile2 >= zui.STRIP_TILE_MIN, tile2)
    check('the smallest window still shows twelve pictures',
          len([w for w in narrow if int(w.grid_info()['row']) < zui.STRIP_FIT_COLS]) >= 12,
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
    check('and the canvas asks for it on top of CONTENTS_W',
          pane.winfo_reqwidth() >= zui.CONTENTS_W + zui.App._bar_px(bar),
          (pane.winfo_reqwidth(), zui.CONTENTS_W, zui.App._bar_px(bar)))
    # a pane narrowed by the window must re-lay out, not leave its last column outside the canvas
    root.geometry('1300x760+20+20')
    pump(root, app, 1.0)
    pane_cells = cells(app, app.contents)
    check('a resized pane reflows inside its new width',
          right_edge(pane_cells) <= app.contents_canvas.winfo_width(),
          (right_edge(pane_cells), app.contents_canvas.winfo_width()))
    # The pane is a fixed width now (the sash sets it), so "hands its column back" is measured where it happens: the
    # column loses its width and the strip's canvas gains it.
    open_canvas = app.gal_canvas.winfo_width()
    pane_w = app.contents_canvas.winfo_width()
    app.select_folder(None)
    pump(root, app, 0.6)
    check('a hidden pane hands its column width back to the strip',
          int(app.gal.grid_columnconfigure(2)['weight']) == 0
          and int(app.gal.grid_columnconfigure(2)['minsize']) == 0
          and app.gal_canvas.winfo_width() - open_canvas >= pane_w * 0.7,
          (app.gal.grid_columnconfigure(2), open_canvas, app.gal_canvas.winfo_width(), pane_w))
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
    # in size and the right column is cut off" report was tiles composed at 337 px still sitting in a 343 px canvas
    app.select_folder(None)
    pump(root, app, 1.0)
    check('closing the pane re-lays the folder tiles out for the wider canvas', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width()))
    app.select_folder(0)
    pump(root, app, 1.0)
    check('and opening it again re-lays them out for the narrower one', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width()))
    root.state('normal')
    root.geometry('1200x700+20+20')
    pump(root, app, 1.4)
    check('a narrower window re-lays them out too', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width()))
    root.state('zoomed')
    pump(root, app, 1.4)
    check('and so does a wider one', tiles_fit(),
          (right_edge(app._folder_tiles()), app.gal_canvas.winfo_width()))

    # ---- L2: the splitter between the folder pane and the strip
    print('\n-- splitter: a sash with a hit area, clamps, keys and a remembered width')
    app.select_folder(0)
    pump(root, app, 1.0)
    sash = app.contents_sash

    def sash_press_release():
        x, y = sash.winfo_rootx() + sash.winfo_width() // 2, sash.winfo_rooty() + 20
        sash.event_generate('<Button-1>', x=2, y=20, rootx=x, rooty=y)
        root.update()

    def sash_drag(dx, steps=8):
        x, y = sash.winfo_rootx() + sash.winfo_width() // 2, sash.winfo_rooty() + 20
        sash.event_generate('<Button-1>', x=2, y=20, rootx=x, rooty=y)
        root.update()
        for i in range(steps):
            sash.event_generate('<B1-Motion>', x=2, y=20, rootx=x + int(dx * (i + 1) / steps), rooty=y)
            root.update()
        sash.event_generate('<ButtonRelease-1>', x=2, y=20, rootx=x + dx, rooty=y)
        root.update()

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

    app._set_contents_w(300)
    pump(root, app, 0.5)
    pane_before = app.contents_canvas.winfo_width()
    sash_drag(40, steps=8)                                  # to the right: the pane narrows
    pump(root, app, 0.9)
    check('a drag moves the boundary', app._contents_w == 260, app._contents_w)
    check('the pane canvas follows the drag',
          abs(app.contents_canvas.winfo_width() - (pane_before - 40)) <= 3,
          (app.contents_canvas.winfo_width(), pane_before))
    check('and the width is written to the settings file',
          zui.load_settings().get('contents_w') == 260, zui.load_settings().get('contents_w'))
    sash_drag(600, steps=12)                                # past the narrow end
    pump(root, app, 0.9)
    check('dragging past the narrow end stops at 180 px',
          app._contents_w == zui.CONTENTS_W_MIN
          and app.contents_canvas.winfo_width() >= zui.CONTENTS_W_MIN,
          (app._contents_w, app.contents_canvas.winfo_width()))
    sash_drag(-600, steps=12)                               # past the wide end
    pump(root, app, 0.9)
    limit = app._contents_w_limit()
    cols, tile = app._strip_metrics()
    canvas_w = app.gal_canvas.winfo_width()
    check('dragging past the wide end stops at what this window can pay for',
          app._contents_w == limit and zui.CONTENTS_W_MIN < limit <= zui.CONTENTS_W_MAX,
          (app._contents_w, limit))
    check('four strip columns survive at the 96 px never-shrink floor',
          cols == zui.STRIP_FIT_COLS and tile >= zui.STRIP_TILE_MIN, (cols, tile, canvas_w))
    check('and the four of them still fit the canvas the pane left',
          cols * (tile + zui.TILE_CHROME) <= canvas_w, (cols * (tile + zui.TILE_CHROME), canvas_w))
    x, y = sash.winfo_rootx() + sash.winfo_width() // 2, sash.winfo_rooty() + 20
    for _ in range(2):
        sash.event_generate('<Button-1>', x=2, y=20, rootx=x, rooty=y)
        root.update()
        sash.event_generate('<ButtonRelease-1>', x=2, y=20, rootx=x, rooty=y)
        root.update()
    pump(root, app, 0.6)
    check('a double-click restores 300 px', app._contents_w == zui.CONTENTS_W, app._contents_w)

    app._set_contents_w(200, persist=True)
    pump(root, app, 0.4)
    sash.focus_set()
    root.update()
    sash.event_generate('<Left>')
    root.update()
    check('Left moves the boundary 16 px', app._contents_w == 216, app._contents_w)
    sash.event_generate('<Right>')
    root.update()
    check('Right moves it back 16', app._contents_w == 200, app._contents_w)
    sash.event_generate('<Shift-Left>')
    root.update()
    check('Shift+Left moves it 64', app._contents_w == 264, app._contents_w)
    sash.event_generate('<Shift-Right>')
    root.update()
    check('Shift+Right moves it back 64', app._contents_w == 200, app._contents_w)
    sash.event_generate('<Home>')
    pump(root, app, 0.4)
    check('Home goes to the narrow end', app._contents_w == zui.CONTENTS_W_MIN, app._contents_w)
    sash.event_generate('<End>')
    pump(root, app, 0.4)
    check('End goes to the wide end', app._contents_w == app._contents_w_limit(), app._contents_w)

    # no flicker: a drag is a burst of <Configure> events and the 50 ms debounce has to absorb it
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
        app._set_contents_w(300)
        pump(root, app, 0.6)
        counts.update(refresh=0, tiles=0, resize=0)
        sash_drag(-40, steps=40)
        pump(root, app, 1.4)
        total = counts['refresh'] + counts['tiles']
        check('a 40-step drag queues a bounded number of rebuilds', total <= 4, dict(counts))
        check('and no burst of them while the pointer is down', counts['resize'] <= 4, dict(counts))
        check('the strip bar keeps its grid cell through the drag',
              app.gal_bar.winfo_manager() == 'grid', app.gal_bar.winfo_manager())
    finally:
        zui.App.refresh_gallery, zui.App._render_folder_tiles, zui.App._apply_strip_resize = (
            real_refresh, real_tiles, real_resize)

    app._set_contents_w(250, persist=True)
    pump(root, app, 0.5)
    check('the width survives a full persist',
          zui.load_settings().get('contents_w') == 250, zui.load_settings().get('contents_w'))
    root10 = tk.Tk()
    app10 = zui.App(root10, Stub(), args)
    # only this window's geometry is under test: a second root must not paint (Tkinter's default root is the *first*
    # one, so a folder sheet built here would be an image the second interpreter cannot resolve)
    app10._closing = True
    root10.update()
    check('a new window opens at the width the last one was left at',
          app10._contents_w == 250, app10._contents_w)
    check('and its pane asks for it on top of its scrollbar',
          app10.contents_canvas.winfo_reqwidth()
          == 250 + zui.App._bar_px(app10.contents_bar),
          (app10.contents_canvas.winfo_reqwidth(), zui.App._bar_px(app10.contents_bar)))
    root10.destroy()
    app._set_contents_w(zui.CONTENTS_W, persist=True)
    pump(root, app, 0.4)

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
