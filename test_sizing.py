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
    app.select_folder(None)
    pump(root, app, 0.4)
    check('a hidden pane hands its column width back to the strip',
          int(app.gal.grid_columnconfigure(2)['weight']) == 0,
          app.gal.grid_columnconfigure(2))
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
    root.destroy()

    print('\n%d failure(s)' % len(FAILS))
    for f in FAILS:
        print('  FAILED: %s' % f)
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
