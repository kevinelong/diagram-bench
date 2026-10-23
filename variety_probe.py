"""variety_probe.py - how different are images from one prompt, and what changes that?

Generates a fixed set of seeds per condition, then reports three independent measures of difference:

  detail        full-resolution mean absolute difference - texture and small detail
  composition   16x16 greyscale mean absolute difference - layout, framing, where things sit
  palette       per-channel histogram distance           - whether the colour world changed

They behave very differently, which is the point: detail barely moves between models, while palette is what tells you
the scene itself changed. Results land as JSON plus a contact sheet per condition, so a claim can be re-checked.

    python variety_probe.py --prompt "a red bicycle" --prompt "a red bicycle leaning against a weathered brick wall at golden sunset"
    python variety_probe.py --prompt "a red bicycle" --profile "flux1 dev" --steps 20 --cfg 3.5

Images are written to the system temp folder (they are large); only the JSON and the sheets go to --out.
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zimage_gen as zg  # noqa: E402
import zimage_ui as zui  # noqa: E402


def detail(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def composition(a, b):
    ga = a.convert('L').resize((16, 16), Image.LANCZOS)
    gb = b.convert('L').resize((16, 16), Image.LANCZOS)
    return sum(ImageStat.Stat(ImageChops.difference(ga, gb)).mean)


def palette(a, b):
    total = 0.0
    for ch in range(3):
        ha, hb = a.getchannel(ch).histogram(), b.getchannel(ch).histogram()
        total += sum(abs(x - y) for x, y in zip(ha, hb)) / (a.width * a.height)
    return total / 3


def measure(paths):
    imgs = [Image.open(p).convert('RGB') for p in paths]
    pairs = list(itertools.combinations(range(len(imgs)), 2))
    out = {}
    for name, fn in (('detail', detail), ('composition', composition), ('palette', palette)):
        vals = [fn(imgs[i], imgs[j]) for i, j in pairs]
        out[name] = {'mean': round(statistics.mean(vals), 2), 'min': round(min(vals), 2),
                     'median': round(statistics.median(vals), 2), 'max': round(max(vals), 2)}
    return out, imgs


def shoot(port, prompt, seeds, profile, steps, cfg, size, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob('*'):
        f.unlink()
    engine = zui.Engine(port, autostart=False, progress=False)
    engine.ready = True
    paths, walls = [], []
    for seed in seeds:
        t0 = time.time()
        engine.generate(prompt, size, size, steps, cfg, seed, str(outdir), batch=1, sequential=True,
                        profile=profile)
        while time.time() - t0 < 600:
            try:
                kind, payload = engine.events.get(timeout=1)
            except Exception:
                continue
            if kind == 'image':
                paths.append(Path(payload[1]))
                walls.append(round(time.time() - t0, 1))
                break
            if kind == 'error':
                raise SystemExit('%s failed: %s' % (profile, str(payload)[:200]))
    return paths, walls


def sheet(imgs, path, per_row=4):
    w, h = imgs[0].size
    rows = (len(imgs) + per_row - 1) // per_row
    board = Image.new('RGB', (w * per_row, h * rows), 'white')
    for i, im in enumerate(imgs):
        board.paste(im, ((i % per_row) * w, (i // per_row) * h))
    target = (1024, int(1024 * board.height / board.width))
    board.resize(target, Image.LANCZOS).save(path, quality=88)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prompt', action='append', required=True, help='repeatable; one condition per prompt')
    ap.add_argument('--profile', default=zg.DEFAULT_MODEL, choices=list(zg.MODELS))
    ap.add_argument('--steps', type=int, default=4)
    ap.add_argument('--cfg', type=float, default=None, help='defaults to the profile\'s own cfg')
    ap.add_argument('--size', type=int, default=512)
    ap.add_argument('--count', type=int, default=8, help='images per condition')
    ap.add_argument('--first-seed', type=int, default=1000)
    ap.add_argument('--port', type=int, default=8006)
    ap.add_argument('--out', default='variety', help='where the JSON and sheets go')
    args = ap.parse_args()

    cfg = args.cfg if args.cfg is not None else zg.MODELS[args.profile]['cfg']
    seeds = list(range(args.first_seed, args.first_seed + args.count))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    images_root = Path(tempfile.gettempdir()) / 'variety-images'

    print('starting server...', flush=True)
    zg.start_server(args.port, timeout=300)
    results = {'profile': args.profile, 'steps': args.steps, 'cfg': cfg, 'size': args.size,
               'seeds': seeds, 'created': datetime.now().isoformat(timespec='seconds'), 'conditions': []}
    try:
        for prompt in args.prompt:
            slug = 'p%02d' % (len(results['conditions']) + 1)
            label = prompt if len(prompt) <= 60 else prompt[:57] + '...'
            print('\n[%s] %d words: %s' % (slug, len(prompt.split()), prompt), flush=True)
            paths, walls = shoot(args.port, prompt, seeds, args.profile, args.steps, cfg, args.size,
                                 images_root / slug)
            stats, imgs = measure(paths)
            sheet(imgs, out / ('%s.jpg' % slug))
            entry = {'slug': slug, 'prompt': prompt, 'words': len(prompt.split()),
                     'seconds_per_image': round(statistics.mean(walls), 1),
                     'images': [p.name for p in paths], 'metrics': stats}
            results['conditions'].append(entry)
            print('   detail %6.2f | composition %6.2f | palette %6.2f | %.1f s/image'
                  % (stats['detail']['mean'], stats['composition']['mean'], stats['palette']['mean'],
                     entry['seconds_per_image']), flush=True)
    finally:
        print('server:', zg.stop_server(args.port), flush=True)

    (out / 'result.json').write_text(json.dumps(results, indent=1), encoding='utf-8')
    print('\nwrote %s and %d sheet(s)' % (out / 'result.json', len(results['conditions'])))
    if len(results['conditions']) > 1:
        a, b = results['conditions'][0], results['conditions'][-1]
        print('ratio (last/first): detail %.2fx  composition %.2fx  palette %.2fx'
              % (b['metrics']['detail']['mean'] / max(0.01, a['metrics']['detail']['mean']),
                 b['metrics']['composition']['mean'] / max(0.01, a['metrics']['composition']['mean']),
                 b['metrics']['palette']['mean'] / max(0.01, a['metrics']['palette']['mean'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
