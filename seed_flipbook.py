"""seed_flipbook.py - sweep the seed and measure whether it behaves like a path.

Generates N tiles with consecutive seeds from one prompt, then reports the mean pixel difference at
increasing seed distance. If seeds formed a trajectory, distance 1 would sit well below distance 8; measured
on Z-Image Turbo at 512 px the two are within 8 % of each other, so a seed sweep is a set of independent
draws (it flickers) rather than a drift (it does not travel).

    python seed_flipbook.py out/dir --count 24 --first 9000 --prompt "flat vector icon of an animal"

Writes out/dir/0001.png ... in seed order plus out/dir/drift.json.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zimage_gen as zg  # noqa: E402
import zimage_ui as zui  # noqa: E402


def one(port, prompt, seed, size, steps, outdir):
    e = zui.Engine(port, autostart=False, progress=False)
    e.ready = True
    t0 = time.time()
    e.generate(prompt, size, size, steps, 1.0, seed, str(outdir))
    while time.time() - t0 < 600:
        try:
            kind, payload = e.events.get(timeout=1)
        except Exception:
            continue
        if kind == 'image':
            return Image.open(Path(payload[1]).with_suffix('.png')).convert('RGB')
        if kind == 'error':
            raise RuntimeError(payload)
    raise TimeoutError('seed %d produced nothing in 600 s' % seed)


def diff(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('--prompt', default='flat vector icon of an animal, simple shapes, minimal, white background')
    ap.add_argument('--count', type=int, default=24)
    ap.add_argument('--first', type=int, default=9000)
    ap.add_argument('--size', type=int, default=512)
    ap.add_argument('--steps', type=int, default=4)
    ap.add_argument('--port', type=int, default=8021)
    a = ap.parse_args()

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    seeds = list(range(a.first, a.first + a.count))
    frames = []
    zg.start_server(a.port, timeout=300)
    try:
        for i, s in enumerate(seeds):
            frames.append(one(a.port, a.prompt, s, a.size, a.steps, out))
            print('  seed %d (%d/%d)' % (s, i + 1, len(seeds)), flush=True)
    finally:
        print('server:', zg.stop_server(a.port))

    for i in range(len(frames)):
        frames[i].save(out / ('%04d.png' % (i + 1)))
    for f in out.glob('*.png'):
        if f.name[:4].isdigit() and int(f.name[:4]) > len(frames):
            f.unlink()

    def mean(pairs):
        return round(sum(pairs) / len(pairs), 2) if pairs else None

    res = {
        'seeds': seeds,
        'consecutive': mean([diff(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]),
        'lag2': mean([diff(frames[i], frames[i + 2]) for i in range(len(frames) - 2)]),
        'lag8': mean([diff(frames[i], frames[i + 8]) for i in range(len(frames) - 8)]),
        'far': mean([diff(frames[i], frames[-1 - i]) for i in range(len(frames) // 2)]),
    }
    (out / 'drift.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
