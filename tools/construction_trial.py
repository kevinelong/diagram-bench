#!/usr/bin/env python
"""Which way of asking turns a photo of a figure into construction blocks?

    python tools/construction_trial.py --base C:/.../figure.png --out C:/Users/kevin/AppData/Local/Temp/construction

Five prompt shapes, both models, one strength, one question, so the comparison is about the *wording* and not the knobs.
Renders go to a temp directory - never the gallery - and each is graded by the same vision model the rest of the project
uses, so the report is a table rather than an opinion.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scene_grade  # noqa: E402
import zimage_ui  # noqa: E402

QUESTION = ('Is the figure drawn as simple 3D construction forms - boxes, cylinders, spheres - with no muscle detail '
            'and no clothing?')

# Each variant is a hypothesis about what the encoder can hear:
VARIANTS = [
    ('wooden mannequin', "an articulated wooden artist's mannequin in the same pose: boxy ribcage, cylindrical limbs, "
                         "sphere joints, unshaded, plain grey background"),
    ('grey clay blocks', 'a figure reduced to simple 3D blocks: box pelvis, box ribcage, cylinder arms and legs, '
                         'no muscle, no clothing, no shading, soft grey clay'),
    ('art-school terms', 'mannequinization: structural breakdown and form blocking of the figure into simple volumes'),
    ('wooden + negatives', "an articulated wooden artist's mannequin in the same pose: boxy ribcage, cylindrical limbs, "
                           "sphere joints, no muscle, no clothing, no shading, no face detail, plain grey background"),
    ('construction lines', 'thin construction lines over the figure: centre line, a box around the ribcage, cylinders on '
                           'the limbs, white background, black line only'),
]

MODELS = [('z-image turbo', 9), ('flux2 klein 9b uncensored', 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--out', default=r'C:/Users/kevin/AppData/Local/Temp/construction')
    ap.add_argument('--denoise', type=float, default=0.72)
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--seed', type=int, default=4242)
    ap.add_argument('--report', default='')
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = Path(a.base)

    engine = zimage_ui.Engine(port=8006, progress=False)
    if not engine._answers():
        print('starting ComfyUI…', flush=True)
        engine.start()
        deadline = time.time() + 240
        while time.time() < deadline:
            try:
                kind, payload = engine.events.get(timeout=2)
            except Exception:
                continue
            print('  %s: %s' % (kind, payload), flush=True)
            if kind == 'ready':
                break
            if kind == 'error':
                raise SystemExit('server did not come up: %s' % payload)
    else:
        print('using the server on 8006', flush=True)

    rows = []
    for name, steps in MODELS:
        for label, prompt in VARIANTS:
            print('\n-- %s · %s' % (name, label), flush=True)
            while not engine.events.empty():
                try:
                    engine.events.get_nowait()
                except Exception:
                    break
            before = set(p.name for p in out.glob('*.png'))
            engine.generate(prompt, a.size, a.size, steps, 1.0, a.seed, str(out),
                            base_path=str(base), denoise=a.denoise, profile=name)
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
            answer, reason = scene_grade.ask(child, QUESTION, scene_grade.SCENE_SYSTEM)
            print('   %-44s -> %-9s %s' % (child.name, answer, reason[:70]), flush=True)
            rows.append((name, label, answer, reason))

    lines = ['# Turning a figure photo into construction blocks: which wording works\n',
             'Base `%s`, strength **%.2f**, seed %d, 1024 px, one question per picture: *%s*.\n' % (
                 base.name, a.denoise, a.seed, QUESTION),
             '| model | prompt shape | verdict | grader |', '|---|---|---|---|']
    for name, label, answer, reason in rows:
        lines.append('| %s | %s | **%s** | %s |' % (name.replace('|', '/'), label, answer,
                                                    reason.replace('|', '/')[:90]))
    yes = sum(1 for r in rows if r[2] == 'yes')
    lines.append('')
    lines.append('**%d of %d renders read as construction blocks.**\n' % (yes, len(rows)))
    by_variant = {}
    for name, label, answer, _reason in rows:
        by_variant.setdefault(label, []).append(answer)
    lines.append('| prompt shape | yes / total |')
    lines.append('|---|---|')
    for label, _prompt in VARIANTS:
        got = by_variant.get(label, [])
        lines.append('| %s | %d / %d |' % (label, got.count('yes'), len(got)))
    text = '\n'.join(lines) + '\n'
    if a.report:
        Path(a.report).write_text(text, encoding='utf-8')
    print('\n' + text)


if __name__ == '__main__':
    main()
