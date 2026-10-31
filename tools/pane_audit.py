"""pane_audit.py - why does a small folder draw nothing until you scroll?

Opens the real gallery read-only in a throwaway App (settings redirected), then opens every folder with
only a few pictures and reports the pane's geometry for each:

    items       how many the folder holds
    shown       the page size the renderer is using
    region      the canvas scrollregion (w x h)
    canvas_h    the visible height
    first/last  the y of the first tile and of its bottom edge
    verdict     EMPTY-IN-VIEW when no tile lands inside the visible band

    python tools/pane_audit.py --source C:/Users/kevin/Pictures/zimage-ui --max 6

Read-only: nothing is written except thumbnails that are already cached, and settings go to a temp dir.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import test_gallery_ui as T          # noqa: E402  (the App fixture: StubEngine)
import zimage_ui as zui              # noqa: E402


def settle(app, root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app._poll()
        root.update()
        time.sleep(0.04)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=r'C:/Users/kevin/Pictures/zimage-ui')
    ap.add_argument('--max', type=int, default=6, help='audit folders with at most this many pictures')
    ap.add_argument('--limit', type=int, default=12, help='how many such folders to audit')
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='pane-audit-'))
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')      # never touch the real profile
    root = tk.Tk()
    root.geometry('1600x900')
    args = T.NS(port=8019, size=512, steps=4, cfg=1.0, seed=1, out=str(Path(a.source)),
                initial='', initial_file='', auto=False, no_progress=True)
    app = zui.App(root, T.StubEngine(), args)
    root.update()
    print(f'reading {a.source} ...')
    end = time.monotonic() + 90
    while time.monotonic() < end and not app._plan:
        app._poll()
        root.update()
        time.sleep(0.05)
    if not app._plan:
        print('no plan arrived; is the folder readable?')
        return 1
    groups = app._plan['groups']
    print(f'{len(groups)} folders, {sum(len(g["items"]) for g in groups)} grouped pictures\n')

    small = [(i, g) for i, g in enumerate(groups) if len(g['items']) <= a.max][:a.limit]
    if not small:
        print(f'no folder holds <= {a.max} pictures')
    for i, g in small:
        app.select_folder(i)
        root.update()
        settle(app, root, 0.35)
        canvas = app.contents_canvas
        kids = [w for w in app.contents.winfo_children() if w.winfo_ismapped()]
        region = canvas.cget('scrollregion')
        ch = canvas.winfo_height()
        ys = sorted(w.winfo_y() for w in kids)
        top = ys[0] if ys else None
        bottom = max((w.winfo_y() + w.winfo_height() for w in kids), default=None)
        def has_picture(w):
            for lab in w.winfo_children():
                if getattr(lab, 'image', None):
                    return True
                if has_picture(lab):
                    return True
            return False
        painted = sum(1 for w in kids if has_picture(w))
        in_view = bool(ys) and any(y < ch for y in ys)
        print(f'[{i:3d}] {len(g["items"]):3d} items  painted={painted:3d}  shown={app._content_shown:3d}  '
              f'region={region!r:18s} '
              f'canvas_h={ch:4d}  first_y={top}  last_bottom={bottom}  '
              f'{"EMPTY-IN-VIEW" if not in_view else "ok"}  {(g.get("label") or "")[:40]}')
    root.destroy()
    print(f'\nsettings dir {tmp}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
