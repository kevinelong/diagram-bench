"""hard_arm_kit.py - the test the last synthesis predicted.

Give method B (code -> SVG) the two advantages that method E actually had:
  1. a helper vocabulary whose parameters FORCE the arithmetic  - `line_at(x, y, degrees, length)` with the
     trigonometry spelled out, so an angle is a number the model must compute, not a word it can ignore;
  2. a repair loop driven by SEMANTICS - the checker's own failure text is fed back, because SVG never crashes.

Acceptance is the checker, not the exit status. The prediction on record: the horizontal-cue failure disappears
within one repair round. If it does not, the prediction is wrong and that is the result.

Usage (cmd.exe):  .venv\\Scripts\\python.exe hard_arm_kit.py --model qwen3-coder:30b
"""
import argparse
import json
import math
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OLLAMA = 'http://127.0.0.1:11434/api/generate'
SVG = '{http://www.w3.org/2000/svg}'
SYSTEM = 'You are a technical illustrator. You output ONE complete SVG document and nothing else.'

KIT = """

Draw with this small kit. Use it instead of hand-writing shapes, and do the arithmetic it implies:

  rect(x, y, w, h)            -> <rect x y width="w" height="h">
  circle(cx, cy, r)           -> <circle cx cy r>
  text(x, y, s)               -> <text x y font-family="Arial" font-size="28">s</text>
  line_at(x, y, degrees, length)
       -> a straight line starting at (x, y) pointing at `degrees` measured from the +x axis, drawn downward
          for positive angles, of the given length. Compute both end points:
              x2 = x + length * cos(radians(degrees))
              y2 = y + length * sin(radians(degrees))

Rules: viewBox="0 0 1024 1024"; black strokes, flat fills, white background; no gradients, filters, shadows,
images or external fonts; every label is a real <text> element. Every element must lie inside 0..1024 in both
axes."""


def ask(model, prompt, timeout=900, seed=1234, temperature=0.0):
    body = {'model': model, 'system': SYSTEM, 'prompt': prompt, 'stream': False,
            'options': {'temperature': temperature, 'seed': seed, 'num_predict': 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    return payload.get('response', ''), round(time.monotonic() - t0, 1)


def f(el, a, d=0.0):
    try:
        return float(str(el.get(a, d)).replace('px', ''))
    except Exception:
        return d


def check(svg_text):
    """the acceptance criteria, each returning (name, ok, measured, expected)."""
    root = ET.fromstring(svg_text)
    W = f(root, 'width', 1024) or 1024
    H = f(root, 'height', 1024) or 1024
    out = []

    rects = [(f(r, 'x'), f(r, 'y'), f(r, 'width'), f(r, 'height')) for r in root.iter(SVG + 'rect')]
    table = [r for r in rects if not (r[2] >= 0.9 * W and r[3] >= 0.9 * H) and r[2] > 50 and r[3] > 50]
    table = max(table, key=lambda r: r[2] * r[3]) if table else None
    if table is None:                     # rects banned: take the bounding box of all lines as the table
        xs, ys = [], []
        for ln in root.iter(SVG + 'line'):
            xs += [f(ln, 'x1'), f(ln, 'x2')]
            ys += [f(ln, 'y1'), f(ln, 'y2')]
        if xs:
            table = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    aspect = round(table[2] / table[3], 2) if table and table[3] else None
    out.append(('aspect', aspect is not None and 1.7 <= aspect <= 2.3, aspect, '1.7-2.3'))

    circles = [(f(c, 'cx'), f(c, 'cy'), f(c, 'r')) for c in root.iter(SVG + 'circle')]
    balls = [c for c in circles if 6 <= c[2] <= 30]
    inside = [b for b in balls if table and table[0] <= b[0] <= table[0] + table[2]
              and table[1] <= b[1] <= table[1] + table[3]]
    out.append(('containment', len(balls) > 0 and len(inside) == len(balls),
                '%d/%d inside' % (len(inside), len(balls)), 'all balls inside the table'))

    # the cue: longest thick element; its angle measures the prediction
    best, angle = None, None
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        if tag == 'rect':
            w, h = f(el, 'width'), f(el, 'height')
            if w >= 100 and 4 <= h <= 20:
                cand = ('rect', w, 0.0)
            else:
                continue
        elif tag == 'line':
            x1, y1, x2, y2 = f(el, 'x1'), f(el, 'y1'), f(el, 'x2'), f(el, 'y2')
            length = math.hypot(x2 - x1, y2 - y1)
            if length >= 100 and f(el, 'stroke-width') >= 4:
                cand = ('line', length, math.degrees(math.atan2(y2 - y1, x2 - x1)))
            else:
                continue
        else:
            continue
        if best is None or cand[1] > best[1]:
            best, angle = cand, cand[2]
    ok_angle = angle is not None and 20 <= abs(angle) <= 45
    out.append(('cue_angle', ok_angle, None if angle is None else round(angle, 1), '20-45 degrees'))

    texts = [' '.join(t.itertext()).strip() for t in root.iter(SVG + 'text')]
    out.append(('label_CUE', any('CUE' in t.upper() for t in texts), ', '.join(texts)[:40] or 'none', 'CUE'))
    return out


def failures_text(results):
    lines = []
    for name, ok, measured, expected in results:
        if not ok:
            lines.append('- %s: expected %s, measured %s' % (name, expected, measured if measured is not None else 'nothing matching'))
    return '\n'.join(lines)


LINES_ONLY = """

IMPORTANT: this document must be built from straight line segments, circles and text only. **Do not emit any
<rect> element at all.** Draw the table outline as four <line> segments, and draw the cue stick with
line_at() so its angle is a computed number."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--attempts', type=int, default=3)
    ap.add_argument('--lines-only', action='store_true', help='ban <rect>, forcing the angle-capable primitive')
    args = ap.parse_args()

    task = json.loads((ROOT / 'hard-task-pooltable.json').read_text(encoding='utf-8'))
    out_dir = ROOT / 'hard'
    out_dir.mkdir(exist_ok=True)
    prompt = task['prompt'] + KIT + (LINES_ONLY if args.lines_only else '')
    feedback = ''
    rows = []
    for attempt in range(1, args.attempts + 1):
        # first attempt is deterministic; later attempts get temperature so a repair is not a re-roll of the same text
        reply, secs = ask(args.model, prompt + feedback, seed=1234 + 97 * attempt,
                          temperature=0.0 if attempt == 1 else 0.3)
        m = re.search(r'<svg[\s\S]*?</svg>', reply, re.IGNORECASE)
        if not m:
            rows.append({'attempt': attempt, 'ok': False, 'error': 'no <svg> in reply', 'gen_s': secs})
            print('  attempt %d: no <svg> (%.1fs)' % (attempt, secs), flush=True)
            continue
        svg = m.group(0)
        path = out_dir / ('pooltable-kit%s-try%d.svg' % ('-lines' if args.lines_only else '', attempt))
        path.write_text(svg, encoding='utf-8')
        try:
            results = check(svg)
        except Exception as e:
            rows.append({'attempt': attempt, 'ok': False, 'error': 'parse: %s' % e, 'gen_s': secs})
            print('  attempt %d: parse error %s' % (attempt, e), flush=True)
            continue
        summary = {name: (ok, measured) for name, ok, measured, _ in results}
        failed = [n for n, ok, _, _ in results if not ok]
        rows.append({'attempt': attempt, 'ok': not failed, 'failed': failed, 'measured': summary,
                     'gen_s': secs, 'file': path.name})
        print('  attempt %d: %s  (%s)' % (attempt, 'PASS' if not failed else 'fail: ' + ','.join(failed),
                                          ', '.join('%s=%s' % (n, v[1]) for n, v in summary.items())), flush=True)
        if not failed:
            break
        feedback = ('\n\nYour previous document failed these checks:\n' + failures_text(results) +
                    '\nReturn the complete corrected SVG, fixing exactly those. Keep everything that already passed.')

    name = 'result-hard-kit-lines.json' if args.lines_only else 'result-hard-kit.json'
    (ROOT / name).write_text(json.dumps({'model': args.model, 'lines_only': args.lines_only, 'attempts': rows},
                                        indent=1), encoding='utf-8')
    print('\npassing attempt: %s' % next((r['attempt'] for r in rows if r.get('ok')), 'none'))


if __name__ == '__main__':
    sys.exit(main())
