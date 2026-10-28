#!/usr/bin/env python
"""Does a drawing-medium style keep the picture while changing the medium, and at what strength?

    python tools/style_strength_trial.py --base <figure.png> --out <temp dir>

The subject sentence stays fixed and short; only the style suffix and the strength move. Two questions per render:
one for the medium, one for the content, because "it is charcoal" and "it is the same picture" are different claims
and a single verdict would let either hide the other.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scene_grade  # noqa: E402
import zimage_ui  # noqa: E402

SUBJECT = 'a seated woman in a pale dress in a drawing room'

STYLES = {
    'charcoal': ('Is this a charcoal drawing - soft smudged strokes on textured paper - rather than a photograph?',
                 'Is the picture one seated woman in a dress, in a chair, with her legs crossed?'),
    'blue pencil': ('Is this a blue pencil underdrawing - light blue pencil lines on white paper - rather than a '
                    'photograph?',
                    'Is the picture one seated woman in a dress, in a chair, with her legs crossed?'),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--out', default=r'C:/Users/kevin/AppData/Local/Temp/style-strength')
    ap.add_argument('--strengths', default='0.55,0.65,0.72')
    ap.add_argument('--model', default='flux2 klein 9b uncensored', help='klein: 4 steps, and it drew best so far')
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--seed', type=int, default=4244)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    steps = 4 if 'klein' in a.model else 9
    strengths = [float(s) for s in a.strengths.split(',')]

    engine = zimage_ui.Engine(port=8006, progress=False)
    if not engine._answers():
        raise SystemExit('start the server first')

    rows = []
    for style, (q_medium, q_content) in STYLES.items():
        for strength in strengths:
            prompt = '%s, %s' % (SUBJECT, zimage_ui.STYLES[style])
            print('\n-- %s at %.2f' % (style, strength), flush=True)
            while not engine.events.empty():
                try:
                    engine.events.get_nowait()
                except Exception:
                    break
            before = set(p.name for p in out.glob('*.png'))
            engine.generate(prompt, a.size, a.size, steps, 1.0, a.seed, str(out),
                            base_path=str(Path(a.base)), denoise=strength, profile=a.model)
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
                rows.append((style, strength, 'no image', '', ''))
                continue
            medium, m_reason = scene_grade.ask(child, q_medium, scene_grade.SCENE_SYSTEM)
            content, c_reason = scene_grade.ask(child, q_content, scene_grade.SCENE_SYSTEM)
            print('   %-44s medium %-4s | content %-4s | %s' % (child.name, medium, content, m_reason[:44]), flush=True)
            rows.append((style, strength, medium, content, m_reason))

    print('\n| style | strength | reads as the medium | keeps the content |')
    print('|---|---|---|---|')
    for style, strength, medium, content, _reason in rows:
        print('| %s | %.2f | **%s** | %s |' % (style, strength, medium, content))
    both = [r for r in rows if r[2] == 'yes' and r[3] == 'yes']
    print('\n**%d of %d kept the picture and changed the medium.**' % (len(both), len(rows)))
    for style in STYLES:
        ok = [r[1] for r in both if r[0] == style]
        print('- %s: %s' % (style, ('lowest working strength %.2f' % min(ok)) if ok else 'none of the ladder'))


if __name__ == '__main__':
    main()
