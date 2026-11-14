"""Capture the sash work for the note: a drag in progress, and the keyboard Home/End contract.

    python tools/shot_sash.py [outdir]

Same convention as `tools/shot_sizing.py`: its own temporary gallery, its own settings directory, thumbnails built
before the window opens so the strip is filled on its first frame, and `ImageGrab` cropped to the window. Three shots:

    sash-drag      a real press through the binding plus one motion: the boundary has moved, and the grip is tinted
    sash-end       `<End>` on the strip|pane sash: the pane at its floor (0), the strip taking the gallery column
    sash-home      `<Home>` on the same sash: the other end of the range, the pane at its widest

Written 2026-11-14 with the capture/coalescing work; the numbers printed beside each shot are the ones the suite
asserts, so a stale picture is visible as a mismatch rather than as a nice picture of something else.
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


def settle(root, app, rounds=40):
    for _ in range(rounds):
        try:
            app._poll()
        except Exception:
            pass
        root.update()
        time.sleep(0.03)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix='shots-'))
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='shot-gallery-'))
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')
    paths = make_gallery(tmp / 'gallery')
    zui.thumbs_for([str(p) for p in paths], cache_root=None, build=True)
    root = tk.Tk()
    args = NS(port=8019, size=512, steps=4, cfg=1.0, seed=1, out=str(tmp / 'gallery'),
              initial='', initial_file='', auto=False, no_progress=True)
    app = zui.App(root, Stub(), args)
    settle(root, app, 90)
    # the pictures screen, the way the screen button does - the app opens on the folders list
    app.set_view_mode('columns', persist=False)
    app.toggle_groups(False)
    settle(root, app)
    app._prefetch('strip')
    for _ in range(400):
        app._poll()
        root.update()
        if not app._pending_tiles:
            break
        time.sleep(0.03)
    settle(root, app)
    sash = app.contents_sash
    print('sash: %d px wide at tk scaling %.3f, k = %.3f' % (sash.winfo_width(),
                                                            float(root.tk.call('tk', 'scaling')),
                                                            app._k if hasattr(app, '_k') else 0.0))

    # 1. a drag in progress: the press through the sash's own binding, then one motion of +90 px. Nothing is released,
    #    so the grip is still tinted and the boundary is mid-gesture - which is the state the screenshot is about.
    before = app._contents_w
    x = sash.winfo_rootx() + sash.winfo_width() // 2
    y = sash.winfo_rooty() + 20
    sash.event_generate('<Button-1>', x=2, y=20, rootx=x, rooty=y)
    settle(root, app, 6)
    captured = sash.grab_current() is sash
    sash.event_generate('<B1-Motion>', x=2, y=20, rootx=x - 90, rooty=y)
    settle(root, app, 6)
    grip = str(sash.grip.cget('background'))
    grab(root, out, 'sash-drag')
    print('drag: pane %d -> %d px, capture %s, grip %s' % (before, app._contents_w, captured, grip))
    sash.event_generate('<ButtonRelease-1>', x=2, y=20, rootx=x - 90, rooty=y)
    settle(root, app, 20)

    # 2. the keys: End takes the pane to its floor, Home to the other end of its range. Both through the binding.
    sash.focus_set()
    settle(root, app, 6)
    sash.event_generate('<End>')
    settle(root, app, 20)
    grab(root, out, 'sash-end')
    print('End: pane %d px (floor %d), strip %d px' % (app._contents_w, app._column_floors()[zui.COL_PANE],
                                                       app._strip_w))
    sash.event_generate('<Home>')
    settle(root, app, 20)
    grab(root, out, 'sash-home')
    print('Home: pane %d px (limit %d), strip %d px' % (app._contents_w, app._contents_w_limit(), app._strip_w))
    root.destroy()
    return 0


if __name__ == '__main__':
    sys.exit(main())
