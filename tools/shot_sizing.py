"""Capture the two sized screens for docs/BUGS-20261030-tile-size.md.

    python tools/shot_sizing.py [outdir]

Builds a throwaway gallery under tempfile.mkdtemp, opens the real window on it, and grabs:

    strip-1x.png / strip-2x.png      the picture strip, four across by three down
    finish-1x.png / finish-2x.png    the "Ranked by wins" grid

1x is this host's own DPI (tk scaling untouched); 2x sets `tk scaling` to 2.0 and rebuilds the window. Nothing is
written outside the temp gallery and the output folder.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace as NS

from PIL import Image, ImageDraw, ImageGrab

os.environ['ZIMAGE_SETTINGS_DIR'] = tempfile.mkdtemp(prefix='shot-set-')
os.environ['ZIMAGE_NO_SETTINGS'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pair_review  # noqa: E402
import zimage_ui as zui  # noqa: E402

pair_review.STATE_DIR = Path(tempfile.mkdtemp(prefix='shot-state-'))


class Stub:
    def __init__(self):
        import queue
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
        hue = (i * 37) % 256
        im = Image.new('RGB', (480, 360), (40 + hue % 190, 60 + (hue * 3) % 170, 200 - (hue * 2) % 180))
        d = ImageDraw.Draw(im)
        d.rectangle([24, 24, 456, 336], outline=(250, 250, 250), width=4)
        d.text((40, 40), 'image %02d' % i, fill=(255, 255, 255))
        p = folder / ('20261030-1200%02d_512x512_seed%d.png' % (i, 100 + i))
        im.save(p)
        p.with_suffix('.json').write_text(json.dumps({'prompt': 'a picture number %d' % i, 'seed': 100 + i,
                                                      'created': '2026-10-30T12:%02d:00' % (i % 60)}),
                                          encoding='utf-8')
        os.utime(p, (base + i * 10, base + i * 10))
        paths.append(p)
    return paths


def grab(root, out, name):
    root.update_idletasks()
    root.update()
    root.lift()
    root.attributes('-topmost', True)
    root.update()
    time.sleep(0.25)
    root.update()
    img = ImageGrab.grab()
    scale = img.width / float(root.winfo_screenwidth() or 1)
    x, y = int(root.winfo_rootx() * scale), int(root.winfo_rooty() * scale)
    w, h = int(root.winfo_width() * scale), int(root.winfo_height() * scale)
    crop = img.crop((max(0, x), max(0, y), min(img.width, x + w), min(img.height, y + h)))
    path = out / (name + '.png')
    crop.save(path)
    root.attributes('-topmost', False)
    print('%-22s %s  %s  window %s' % (name, crop.size, path, root.winfo_geometry()))
    return crop


def run(out, scaling, tag):
    tmp = Path(tempfile.mkdtemp(prefix='shot-gallery-'))
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')
    paths = make_gallery(tmp / 'gallery')
    root = tk.Tk()
    if scaling:
        root.tk.call('tk', 'scaling', scaling)
    # build the thumbnails before the window opens: the paint path only reads existing ones, so the strip is filled
    # on its first frame instead of racing the background builder (a screenshot of placeholders proves nothing)
    zui.thumbs_for([str(p) for p in paths], cache_root=None, build=True)
    args = NS(port=8019, size=512, steps=4, cfg=1.0, seed=1, out=str(tmp / 'gallery'),
              initial='', initial_file='', auto=False, no_progress=True)
    app = zui.App(root, Stub(), args)
    for _ in range(90):
        app._poll()
        root.update()
        time.sleep(0.03)
    app.toggle_groups(False)
    for _ in range(40):
        app._poll()
        root.update()
        time.sleep(0.03)
    # thumbnails are built off the UI thread: ask for them, then wait until nothing is still a placeholder
    app._prefetch('strip')
    for _ in range(400):
        app._poll()
        root.update()
        if not app._pending_tiles:
            break
        time.sleep(0.03)
    for _ in range(40):
        app._poll()
        root.update()
        time.sleep(0.03)
    cols, tile = app._strip_metrics()
    print('%s strip: canvas %dx%d, %d columns, tile %d px'
          % (tag, app.gal_canvas.winfo_width(), app.gal_canvas.winfo_height(), cols, tile))
    strip = grab(root, out, 'strip-' + tag)

    win = tk.Toplevel(root)
    win.title('Ranked by wins')
    win.state('zoomed')
    win.columnconfigure(0, weight=1)
    win.columnconfigure(1, weight=1)
    win.rowconfigure(0, weight=1)
    left, right = tk.Label(win), tk.Label(win)
    left.grid(row=0, column=0, sticky='nsew')
    right.grid(row=0, column=1, sticky='nsew')
    win.update()
    shell = {'win': win, 'state': {'key': 'shot', 'paths': [str(p) for p in paths], 'pairs': [], 'deleted': []},
             'left_lbl': left, 'right_lbl': right, 'buttons': (), 'gallery_buttons': (), 'badge': None,
             'deleted': 0, 'queue': None, 'path': None, 'cur': 0}
    app._review = shell
    app._review_finish(shell, decided=7, total=20, deletes=0)
    app._prefetch_paths(list(shell['state']['paths']))
    for _ in range(400):
        win.update()
        app._poll()
        tiles = shell.get('finish_tiles') or []
        if tiles and all(getattr(x, 'image', None) is not None for x in tiles):
            break
        time.sleep(0.03)
    for _ in range(40):
        win.update()
        app._poll()
        time.sleep(0.03)
    print('%s finish: canvas %dx%d, tile %s px, %d slots'
          % (tag, shell['finish_canvas'].winfo_width(), shell['finish_canvas'].winfo_height(),
             shell.get('finish_size'), len(shell.get('finish_tiles') or [])))
    finish = grab(win, out, 'finish-' + tag)
    win.destroy()
    root.destroy()
    return strip, finish


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix='shots-'))
    out.mkdir(parents=True, exist_ok=True)
    probe = tk.Tk()
    scale = probe.tk.call('tk', 'scaling')
    probe.destroy()                       # one root at a time: a stray default root breaks every PhotoImage after it
    print('host tk scaling: %s (screen %sx%s)' % (scale, probe.winfo_screenwidth() if False else '?', '?'))
    for scaling, tag in ((None, '1x'), (2.0, '2x')):
        run(out, scaling, tag)
    print('\nsaved to %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
