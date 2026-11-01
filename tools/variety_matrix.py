"""variety_matrix.py - one seed, a sampler x scheduler matrix, and a number for how different the tiles are.

Asked 2026-10-31: what gives more variety in a 16-up besides seed and prompt. On a distilled 4-step model `cfg` and
`steps` are inert, so this varies the two inputs the server actually honours - `sampler_name` and `scheduler` - holds the
seed at one value, and then measures the result rather than eyeballing it:

    distinctness   median and minimum pairwise dHash distance across the tiles (a difference hash over the thumbnail,
                   plus four quantised quadrant means, which is what `zimage_ui._signature` uses); near-duplicates are
                   pairs under 16 bits, the bar the app already treats as "the same picture"
    detail         Laplacian variance per tile, the quality proxy used for the upscalers

    python tools/variety_matrix.py --samplers 4 --schedulers 4
"""
from __future__ import annotations

import argparse
import sys
import time
import shutil
import itertools
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import zimage_gen as zg           # noqa: E402

SAMPLERS = ['euler', 'euler_ancestral', 'dpm_2_ancestral', 'exp_heun_2_x0_sde']
SCHEDULERS = ['simple', 'sgm_uniform', 'karras', 'exponential']


def _retry(fn, *a, tries=3, wait=5.0, **k):
    """A dropped ComfyUI connection must cost one tile, not the batch — it did on the first run of this script."""
    last = None
    for i in range(tries):
        try:
            return fn(*a, **k)
        except Exception as exc:
            last = exc
            time.sleep(wait * (i + 1))
    raise last


def render(prompt, sampler, scheduler, seed, out_dir, tag):
    dest = Path(out_dir) / ('%s__%s__%s.png' % (tag, sampler, scheduler))
    if dest.exists():                      # resume: a half-finished matrix is not restarted from zero
        return dest, 0.0
    graph = zg.workflow(prompt, 1024, 1024, 4, 1.0, seed, tag, profile='flux2 klein 9b uncensored',
                        sampler=sampler, scheduler=scheduler)
    t0 = time.monotonic()
    try:
        pid = _retry(zg.post, '/prompt', {'prompt': graph})['prompt_id']
    except Exception as exc:
        print('  submit failed (%s) - is ComfyUI up?' % exc)
        return None, time.monotonic() - t0
    images = []
    while time.monotonic() - t0 < 900:
        try:
            h = _retry(zg.get, '/history/' + pid)
        except Exception:
            continue
        if pid in h:
            images = [i for out in (h[pid].get('outputs') or {}).values() for i in out.get('images', [])]
            break
        time.sleep(0.5)
    if not images:
        return None, time.monotonic() - t0
    src = zg.OUTPUT_DIR / images[0].get('subfolder', '') / images[0]['filename']
    shutil.copy2(src, dest)
    return dest, time.monotonic() - t0


def signature(path):
    """The app's own distinction: a 64-bit difference hash plus four quantised quadrant means."""
    from PIL import Image
    with Image.open(path) as im:
        rgb = im.convert('RGB').resize((16, 16), Image.LANCZOS)
        grey = rgb.convert('L').resize((9, 8), Image.LANCZOS)
    px = grey.tobytes()
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (1 if px[y * 9 + x] > px[y * 9 + x + 1] else 0)
    quads = []
    for ox in (0, 8):
        for oy in (0, 8):
            box = rgb.crop((ox, oy, ox + 8, oy + 8)).resize((1, 1), Image.LANCZOS)
            quads.extend(v // 32 for v in box.getpixel((0, 0)))
    return bits, tuple(quads)


def detail(path):
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        g = im.convert('L').resize((1024, 1024), Image.LANCZOS)
    a = np.asarray(g, dtype=np.float32)
    lap = (-4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:])
    return float(lap.var())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prompt', default='a woman in a dark velvet dress seated in a green armchair by a window, '
                                        'soft overcast daylight, photographic')
    ap.add_argument('--seed', type=int, default=1234)
    ap.add_argument('--samplers', type=int, default=len(SAMPLERS))
    ap.add_argument('--schedulers', type=int, default=len(SCHEDULERS))
    ap.add_argument('--out', default=str(Path(r'C:/Users/kevin/AppData/Local/Temp/upscale-lab/variety')))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print('host', zg.resolve_host()[0], '| seed', a.seed)
    made, times = [], {}
    for i, (s, sch) in enumerate(itertools.product(SAMPLERS[:a.samplers], SCHEDULERS[:a.schedulers])):
        tag = 'v%02d' % i
        path, dt = render(a.prompt, s, sch, a.seed, out, tag)
        times[(s, sch)] = dt
        if path is None:
            print('  %-22s %-12s FAILED after %.0fs' % (s, sch, dt))
            continue
        made.append((path, s, sch))
        print('  %-22s %-12s %5.1fs  %s' % (s, sch, dt, path.name))

    if len(made) < 2:
        print('not enough tiles to compare')
        return 1

    sigs = {p: signature(p) for p, _s, _sc in made}
    det = {p: detail(p) for p, _s, _sc in made}
    pairs = list(itertools.combinations([p for p, _s, _sc in made], 2))
    def dist(x, y):
        bx, qx = sigs[x]
        by, qy = sigs[y]
        return bin(bx ^ by).count('1') + sum(abs(i - j) for i, j in zip(qx, qy))
    ds = sorted(dist(x, y) for x, y in pairs)
    near = sum(1 for d in ds if d < 16)
    print('\ntiles %d | pairs %d' % (len(made), len(ds)))
    print('pairwise difference: median %d  min %d  max %d  (near-duplicate pairs under 16: %d)' % (
        ds[len(ds) // 2], ds[0], ds[-1], near))
    print('detail (lap-var): min %.0f  median %.0f  max %.0f' % (
        min(det.values()), sorted(det.values())[len(det) // 2], max(det.values())))
    print('slowest %.1fs  fastest %.1fs' % (max(times.values()), min(times.values())))
    worst = sorted(made, key=lambda t: det[t[0]])[:3]
    print('least detailed tiles:', [(t[1], t[2], round(det[t[0]])) for t in worst])
    Path(out / 'report.txt').write_text(
        'seed %d\ntiles %d\npairs median %d min %d near %d\ndetail min %.0f median %.0f max %.0f\n'
        % (a.seed, len(made), ds[len(ds) // 2], ds[0], near,
           min(det.values()), sorted(det.values())[len(det) // 2], max(det.values())), encoding='utf-8')
    print('\nwrote', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
