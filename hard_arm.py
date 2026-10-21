"""hard_arm.py - run one hard prompt through method B (code -> SVG) and measure what can be measured.

Counts circles, filled circles, rows of balls, the outer rect aspect ratio, angled long shapes and text, so the
task's own checks (declared in hard-task-pooltable.json) become numbers rather than opinions.

Usage (cmd.exe):  .venv\\Scripts\\python.exe hard_arm.py --model qwen3-coder:30b
"""
import argparse
import json
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

RULES = ('\n\nRules: viewBox="0 0 1024 1024"; <rect>, <line>, <circle>, <path>, <text> only; black strokes, flat '
         'fills, white background; no gradients, filters, shadows, images, external fonts or <style> blocks; every '
         'label is a real <text> element with font-family="Arial".')


EXAMPLE = """

Here is a minimal worked example of the document shape expected (adapt it, do not copy its content):

<svg viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
  <rect x="100" y="100" width="824" height="412" fill="none" stroke="black" stroke-width="4"/>
  <circle cx="140" cy="140" r="16" fill="white" stroke="black" stroke-width="3"/>
  <line x1="200" y1="300" x2="360" y2="260" stroke="black" stroke-width="8"/>
  <text x="420" y="330" font-family="Arial" font-size="28">CUE</text>
</svg>
"""


def ask(model, prompt, timeout=900, seed=1234, example=False):
    body = {'model': model, 'system': SYSTEM, 'prompt': prompt + RULES + (EXAMPLE if example else ''), 'stream': False,
            'options': {'temperature': 0, 'seed': seed, 'num_predict': 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    return payload.get('response', ''), round(time.monotonic() - t0, 1)


def num(el, a, d=0.0):
    try:
        return float(str(el.get(a, d)).replace('px', ''))
    except Exception:
        return d


def measure(svg_text):
    root = ET.fromstring(svg_text)
    W = num(root, 'width', 1024) or 1024
    H = num(root, 'height', 1024) or 1024
    circles, small, filled, texts, rects = [], 0, 0, [], []
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        if tag == 'circle':
            r = num(el, 'r')
            fill = str(el.get('fill', 'none')).lower()
            circles.append({'cx': num(el, 'cx'), 'cy': num(el, 'cy'), 'r': r})
            if r <= 12:
                small += 1
            if fill not in ('none', 'white', ''):
                filled += 1
        elif tag == 'text':
            t = ' '.join(el.itertext()).strip()
            if t:
                texts.append(t)
        elif tag == 'rect':
            rects.append({'w': num(el, 'width'), 'h': num(el, 'height'),
                          'x': num(el, 'x'), 'y': num(el, 'y'),
                          'transform': el.get('transform', '')})
    # outer rect = the largest rect that is not the full canvas background
    outer = None
    for r in sorted(rects, key=lambda r: r['w'] * r['h'], reverse=True):
        if not (r['w'] >= 0.9 * W and r['h'] >= 0.9 * H):
            outer = r
            break
    aspect = round(outer['w'] / outer['h'], 2) if outer and outer['h'] else None
    # ball rows: group ball-sized circles by y
    balls = [c for c in circles if 6 <= c['r'] <= 30]
    rows = len({round(c['cy'] / 20) for c in balls})
    # angled long shape: a rect or line rotated, or a rect much longer than tall with a transform
    angled = 0
    for r in rects:
        if r['transform'] and ('rotate' in r['transform']):
            angled += 1
    for el in root.iter(SVG + 'line'):
        dx, dy = abs(num(el, 'x2') - num(el, 'x1')), abs(num(el, 'y2') - num(el, 'y1'))
        if dx > 60 and dy > 60:
            angled += 1
    return {'svg_bytes': len(svg_text.encode()), 'rects': len(rects), 'circles': len(circles),
            'small_circles': small, 'filled_circles': filled, 'ball_sized': len(balls),
            'ball_rows': rows, 'angled_shapes': angled, 'outer_aspect': aspect,
            'text_values': texts[:12], 'has_CUE': any('CUE' in t.upper() for t in texts)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--example', action='store_true')
    args = ap.parse_args()
    task = json.loads((ROOT / 'hard-task-pooltable.json').read_text(encoding='utf-8'))
    reply, secs = ask(args.model, task['prompt'], example=args.example)
    m = re.search(r'<svg[\s\S]*?</svg>', reply, re.IGNORECASE)
    out_dir = ROOT / 'hard'
    out_dir.mkdir(exist_ok=True)
    suffix = '-example' if args.example else ''
    svg_path = out_dir / ('pooltable-%s%s.svg' % (args.model.replace(':', '_'), suffix))
    if not m:
        print('no <svg> in reply (%d chars, %.1fs)' % (len(reply), secs))
        (out_dir / 'pooltable-reply.txt').write_text(reply, encoding='utf-8')
        return
    svg_path.write_text(m.group(0), encoding='utf-8')
    try:
        stats = measure(m.group(0))
    except Exception as e:
        print('parse error:', e)
        return
    stats.update({'model': args.model, 'gen_s': secs, 'task': task['prompt'][:60]})
    print(json.dumps(stats, indent=1))
    (ROOT / 'result-hard-methodb.json').write_text(json.dumps(stats, indent=1), encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
