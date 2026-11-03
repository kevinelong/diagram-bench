"""Build a gallery with the same shape as the desktop's, so ux_timing compares like with like.

Desktop reference (MACHINES-2026-11-02.md): 2,874 pictures, 102 folders, worst folder 540.
This copies the existing renders under generated names with sidecars whose prompts form 102 groups of that
shape. Nothing is written to the real gallery; this one is disposable and regenerated on demand.

    python tools/make_ux_gallery.py --out D:/ux-synthetic --pictures 2874 --folders 102 --worst 540
"""
import argparse
import json
import shutil
from datetime import datetime, timedelta

from pathlib import Path


def build(out, source, pictures, folders, worst):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    srcs = sorted(Path(source).glob('*.png'))
    if not srcs:
        raise SystemExit('no source pictures in %s' % source)

    # 1 folder of `worst`, then the rest spread evenly over what is left - the shape the desktop reports
    rest = pictures - worst
    others = folders - 1
    plan = [worst] + [rest // others + (1 if i < rest % others else 0) for i in range(others)]

    stamp = datetime(2026, 10, 1, 9, 0, 0)
    made = 0
    for f, count in enumerate(plan):
        prompt = ('scene %d: a labelled diagram of a %s with %d parts, drawn in a technical style with clear '
                  'lettering and no gradients' % (f, ['rack', 'valve', 'gear', 'lattice', 'flange'][f % 5], f % 9 + 3))
        for i in range(count):
            src = srcs[made % len(srcs)]
            stem = 'ux%02d-%04d_1024x1024_seed%04d' % (f, i, 1000 + i)
            png = out / (stem + '.png')
            if not png.exists():
                shutil.copyfile(src, png)
            (out / (stem + '.json')).write_text(json.dumps({
                'image': str(png), 'prompt': prompt, 'seed': 1000 + i, 'width': 1024, 'height': 1024,
                'created': (stamp + timedelta(minutes=made)).isoformat(timespec='seconds'),
                'model': 'z-image turbo', 'steps': 9, 'cfg': 1.0, 'base_image': None, 'denoise': 1.0,
                'lineage_root': prompt, 'lineage': [prompt],
            }), encoding='utf-8')
            made += 1
    print('built %d pictures in %d groups (largest %d) at %s' % (made, folders, worst, out))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='D:/ux-synthetic')
    ap.add_argument('--source', default='C:/Users/kevin/Pictures/zimage-ui')
    ap.add_argument('--pictures', type=int, default=2874)
    ap.add_argument('--folders', type=int, default=102)
    ap.add_argument('--worst', type=int, default=540)
    a = ap.parse_args(argv)
    build(a.out, a.source, a.pictures, a.folders, a.worst)


if __name__ == '__main__':
    main()
