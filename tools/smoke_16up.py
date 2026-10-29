#!/usr/bin/env python
"""Smoke: the 16-up really renders sixteen tiles and the sheet, on both paths.

    python tools/smoke_16up.py            # one batch, ~20 s
    python tools/smoke_16up.py --sequential   # one at a time, ~3 min

The suite pins the *dispatch* (`batch == 16` against a stub); only a real run catches a broken sheet assembly or a
per-tile seed that stopped varying. Exits non-zero on any failed check, so it can stand in for a test.
"""

import argparse
import json
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import zimage_ui as zui  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8006)
    ap.add_argument('--size', default='384', help='384 keeps the run short; the tiles are a fixed cost above that')
    ap.add_argument('--sequential', action='store_true')
    ap.add_argument('--layout', default='16-up (4x4)')
    ap.add_argument('--model', default='z-image turbo')
    ap.add_argument('--timeout', type=float, default=600)
    a = ap.parse_args()

    fails = []
    tmp = Path(tempfile.mkdtemp(prefix='smoke16-'))
    import os
    os.environ['ZIMAGE_SETTINGS_DIR'] = tempfile.mkdtemp(prefix='smoke16-set-')

    root = tk.Tk()
    args = NS(port=a.port, size=int(a.size), steps=9, cfg=1.0, seed=1234, out=str(tmp),
              initial='', initial_file='', auto=False, no_progress=True, no_autostart=False)
    engine = zui.Engine(port=a.port, progress=False)
    if not engine._answers():
        raise SystemExit('no server on %d - start start-comfy.ps1 first' % a.port)
    app = zui.App(root, engine, args)
    root.update()

    app.model_var.set(a.model); app._on_model_change()
    app.size_var.set(a.size); app.layout_var.set(a.layout); app.seq_var.set(bool(a.sequential))
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a red fox in snow')
    app.busy = False
    want = app._batch()
    started = time.time()
    app.generate(); root.update()
    while time.time() - started < a.timeout and app.busy:
        root.update(); time.sleep(0.2)

    pngs = sorted(tmp.glob('*.png'))
    tiles = [p for p in pngs if 'sheet' not in p.name]
    sheets = [p for p in pngs if 'sheet' in p.name]
    meta = [json.loads(p.with_suffix('.json').read_text(encoding='utf-8')) for p in tiles]
    seeds = sorted({m.get('seed') for m in meta})

    def check(label, ok, detail=''):
        print('%-58s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
        if not ok:
            fails.append(label)

    print('layout %s, %s, %d tiles wanted' % (a.layout, 'one at a time' if a.sequential else 'one batch', want))
    check('every tile was written', len(tiles) == want, '%d of %d' % (len(tiles), want))
    check('exactly one sheet was written', len(sheets) == 1, str(len(sheets)))
    if sheets:
        from PIL import Image
        side = Image.open(sheets[0]).size
        cols = {4: 2, 9: 3, 16: 4}.get(want) or 4
        tile_px = int(a.size)
        expect = cols * tile_px + (cols - 1) * 6
        check('the sheet is the right size for %d tiles' % want, side[0] == expect and side[1] == expect,
              '%s vs %d' % (side, expect))
    if a.sequential:
        check('sequential tiles get consecutive seeds', seeds == list(range(meta[0].get('seed'), meta[0].get('seed') + want)),
              str(seeds[:3]))
    else:
        check('a single batch shares one seed', len(seeds) == 1, str(seeds))
    check('every tile stayed at the requested size', all((m.get('width'), m.get('height')) == (int(a.size), int(a.size))
                                                         for m in meta), str({(m.get('width'), m.get('height')) for m in meta}))
    print('%.1f s total' % (time.time() - started))
    try:
        root.destroy()
    except Exception:
        pass
    print('files in', tmp)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
