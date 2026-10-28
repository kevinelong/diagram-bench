#!/usr/bin/env python
"""Second pass: draw construction lines over a block figure.

    python tools/two_pass_trial.py --base1 <mannequin.png> --base2 <mannequin.png> --out <temp dir>

One question per render, and it is the literal ask: a figure built from simple 3D blocks **and** construction lines
drawn over it. The first pass produced the blocks (see construction_trial.py); this pass is graded on whether the lines
arrive without the blocks being lost, at two strengths, on both models.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scene_grade  # noqa: E402
import zimage_ui  # noqa: E402

LINES = ('thin construction lines over the figure: centre line, a box around the ribcage, cylinders on the limbs, '
         'white background, black line only')

Q3 = ('Does the image show a figure built from simple 3D blocks (boxes, cylinders, spheres) and also drawn '
      'construction lines or geometric guides over or around it?')

MODELS = [('z-image turbo', 9), ('flux2 klein 9b uncensored', 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base1', required=True, help='the mannequin from the turbo pass')
    ap.add_argument('--base2', required=True, help='the mannequin from the klein pass')
    ap.add_argument('--out', default=r'C:/Users/kevin/AppData/Local/Temp/construction2')
    ap.add_argument('--strengths', default='0.55,0.65')
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--seed', type=int, default=4243)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    basis = {'turbo': Path(a.base1), 'klein': Path(a.base2)}
    strengths = [float(s) for s in a.strengths.split(',')]

    engine = zimage_ui.Engine(port=8006, progress=False)
    if not engine._answers():
        raise SystemExit('start the server first: the first trial already warms it')

    rows = []
    for name, steps in MODELS:
        tag = 'turbo' if 'turbo' in name else 'klein'
        for strength in strengths:
            label = '%s on %s mannequin' % (strength, tag)
            print('\n-- %s · %s' % (name, label), flush=True)
            while not engine.events.empty():
                try:
                    engine.events.get_nowait()
                except Exception:
                    break
            before = set(p.name for p in out.glob('*.png'))
            engine.generate(LINES, a.size, a.size, steps, 1.0, a.seed, str(out),
                            base_path=str(basis[tag]), denoise=strength, profile=name)
            child, deadline = None, time.time() + 600
            while time.time() < deadline:
                try:
                    kind, payload = engine.events.get(timeout=5)
                except Exception:
                    continue
                if kind == 'image':
                    child = Path(payload[1])
                    break
                if kind in ('error', 'cancelled'):
                    print('   %s: %s' % (kind, payload), flush=True)
                    break
            if child is None:
                new = set(p.name for p in out.glob('*.png')) - before
                child = (out / sorted(new)[-1]) if new else None
            if child is None:
                rows.append((name, label, 'no image', ''))
                continue
            answer, reason = scene_grade.ask(child, Q3, scene_grade.SCENE_SYSTEM)
            print('   %-44s -> %-9s %s' % (child.name, answer, reason[:70]), flush=True)
            rows.append((name, label, answer, reason))

    print('\n| model | second pass | blocks + lines | grader |')
    print('|---|---|---|---|')
    for name, label, answer, reason in rows:
        print('| %s | %s | **%s** | %s |' % (name, label, answer, reason.replace('|', '/')[:88]))
    yes = sum(1 for r in rows if r[2] == 'yes')
    print('\n**%d of %d second passes produced blocks with construction lines.**' % (yes, len(rows)))


if __name__ == '__main__':
    main()
