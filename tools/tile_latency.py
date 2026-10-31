"""tile_latency.py - how long after a `tile` event does that tile show a picture?

Reported twice (2026-10-31): during an N-up run the strip shows "just some words" instead of the pictures as they
land. The paint path never decodes by design, so a freshly written picture has no thumbnail until `_warm_one` builds
one and the coalesced repaint runs. This measures that latency per tile, with the two cadences a real run can have:

    --gap 0.7    one tile per generation step (the measured BATCH_SECONDS_PER_TILE)
    --gap 0.0    all sixteen back to back, the harshest case

Nothing here needs a GPU or a server: the tile bytes are written straight into the gallery and delivered through the
same `('tile', ...)` event the engine posts.

    python tools/tile_latency.py --gap 0.7 --px 1024
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
import test_gallery_ui as T          # noqa: E402
import zimage_ui as zui              # noqa: E402


def has_picture(widget):
    if getattr(widget, 'image', None) is not None:
        return True
    return any(has_picture(c) for c in widget.winfo_children())


def painted_for(app, path):
    """Is there a widget for this path, and does it hold a picture?"""
    lab = (getattr(app, '_tile_widgets', {}) or {}).get(Path(path))
    if lab is None:
        for k, v in (getattr(app, '_tile_widgets', {}) or {}).items():
            if str(k) == str(path):
                lab = v
                break
    if lab is None:
        return None
    return has_picture(lab)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gap', type=float, default=0.7)
    ap.add_argument('--px', type=int, default=1024)
    ap.add_argument('--tiles', type=int, default=16)
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='tile-lat-'))
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')
    os.environ['ZIMAGE_NO_FOCUS'] = '1'
    T.make_gallery(tmp, 8)

    # one realistic source image, copied per tile: same decode cost, cheap to make
    from PIL import Image
    src = tmp / 'src.png'
    img = Image.new('RGB', (a.px, a.px))
    img.putdata([((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
                 for y in range(a.px) for x in range(a.px)])
    img.save(src)
    payload = src.read_bytes()
    print(f'source {a.px}px, {len(payload) / 1e6:.2f} MB; gap {a.gap}s; {a.tiles} tiles')

    root = tk.Tk()
    root.geometry('1500x900')
    args = T.NS(port=8019, size=512, steps=4, cfg=1.0, seed=1, out=str(tmp),
                initial='', initial_file='', auto=False, no_progress=True)
    app = zui.App(root, T.StubEngine(), args)
    root.update()
    end = time.monotonic() + 20
    while time.monotonic() < end and not app._plan:
        app._poll()
        root.update()
        time.sleep(0.05)
    app.busy = True                     # a run in flight, as the engine would set it
    app.refresh_gallery()
    root.update()

    lat, never, first_visible = [], 0, None
    for i in range(a.tiles):
        path = tmp / ('%s_%dx%d_seed%d_%02d.png' % (time.strftime('%Y%m%d-%H%M%S'), a.px, a.px, 4242, i + 1))
        path.write_bytes(payload)
        path.with_suffix('.json').write_text(
            '{"prompt": "a test tile %d", "seed": %d, "size": %d}' % (i, 4242, a.px), encoding='utf-8')
        t0 = time.monotonic()
        app.engine.events.put(('tile', (payload, str(path), i + 1, a.tiles)))
        app._poll()
        root.update()
        got = None
        while time.monotonic() - t0 < 5.0:
            state = painted_for(app, path)
            if state:
                got = time.monotonic() - t0
                break
            app._poll()
            root.update()
            time.sleep(0.02)
        if got is None:
            never += 1
        else:
            lat.append(got)
            if first_visible is None:
                first_visible = got
        if a.gap:
            time.sleep(a.gap)

    root.update()
    total_tiles = len([w for w in app.recent.winfo_children() if w.grid_info()])
    with_pic = len([w for w in app.recent.winfo_children()
                    if w.grid_info() and has_picture(w)])
    print(f'\ntiles painted with a picture: {with_pic} of {total_tiles} widgets in the strip')
    if lat:
        lat.sort()
        print('latency to a visible picture: median %.2fs  p90 %.2fs  max %.2fs  (n=%d, never=%d)'
              % (lat[len(lat) // 2], lat[min(len(lat) - 1, int(len(lat) * 0.9))], lat[-1], len(lat), never))
    else:
        print('no tile ever painted a picture (%d timed out)' % never)
    root.destroy()
    print(f'gallery {tmp}')
    return 0 if (lat and never == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
