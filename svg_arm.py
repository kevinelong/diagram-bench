"""svg_arm.py - method B: a local model writes SVG directly, no image model involved.

Runs the frozen diagram corpus through a local Ollama model and stores what it returns, verbatim, then
records whether it is a valid SVG document. Scoring (render, OCR, structure, trace) happens separately so a
generation run never depends on the scorer being right.

Usage (through cmd.exe):
  .venv\\Scripts\\python.exe svg_arm.py --model qwen3-coder:30b --samples 2
  .venv\\Scripts\\python.exe svg_arm.py --model qwen3.5:9b --samples 1 --only flow-1
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
CORPUS = ROOT / 'prompts-diagrams.json'
OLLAMA = 'http://127.0.0.1:11434/api/generate'

SYSTEM = ('You are a technical illustrator. You output ONE complete SVG document and nothing else: no prose, '
          'no explanation, no markdown fences.')

RULES = ('\n\nRules: use viewBox="0 0 1024 1024" and width="1024" height="1024"; draw with <rect>, <line>, '
         '<path>, <circle> and <text> only; write every label as a real <text> element with font-family="Arial" '
         'and font-size 28 or larger; use only black strokes and solid flat fills on a white background; no '
         'gradients, no filters, no shadows, no images, no external fonts, no <style> blocks; keep every line '
         'horizontal or vertical except where the subject requires otherwise; make labels large enough to read '
         'and do not let text overlap other elements or leave the canvas.')


def extract_svg(text):
    m = re.search(r'<svg[\s\S]*?</svg>', text, re.IGNORECASE)
    return m.group(0) if m else None


def ask(model, prompt, timeout=600, seed=1234):
    body = {'model': model, 'system': SYSTEM, 'prompt': prompt + RULES, 'stream': False,
            'options': {'temperature': 0, 'seed': seed, 'num_predict': 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    return payload.get('response', ''), (time.monotonic() - started), payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--samples', type=int, default=2)
    ap.add_argument('--only', default=None)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    corpus = json.loads(CORPUS.read_text(encoding='utf-8'))
    prompts = [p for p in corpus['prompts'] if not args.only or p['id'] == args.only]
    suffix = corpus['params']['style_suffix']
    outdir = ROOT / 'svg' / args.model.replace(':', '_')
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    for p in prompts:
        for sample in range(args.samples):
            full = p['prompt'] + suffix
            started = time.monotonic()
            try:
                text, seconds, payload = ask(args.model, full, seed=1234 + sample)
            except Exception as e:
                rows.append({'id': p['id'], 'sample': sample, 'ok': False, 'error': type(e).__name__ + ': ' + str(e)})
                print('  %-12s s%d ERROR %s' % (p['id'], sample, e), flush=True)
                continue
            svg = extract_svg(text)
            valid, note = False, 'no <svg> element in reply'
            if svg:
                try:
                    ET.fromstring(svg)
                    valid, note = True, 'parsed'
                except Exception as e:
                    note = 'XML error: %s' % e
            path = outdir / ('%s-s%d.svg' % (p['id'], sample))
            if svg:
                path.write_text(svg, encoding='utf-8')
            row = {'id': p['id'], 'kind': p['kind'], 'sample': sample, 'ok': valid, 'note': note,
                   'seconds': round(seconds, 1), 'svg_bytes': len(svg) if svg else 0,
                   'reply_chars': len(text), 'eval_count': payload.get('eval_count'),
                   'file': str(path.name) if svg else None}
            rows.append(row)
            print('  %-12s s%d %-5s %6.1fs %7d B  %s' % (p['id'], sample, 'VALID' if valid else 'INVALID',
                                                        seconds, row['svg_bytes'], note[:60]), flush=True)

    out = Path(args.out) if args.out else (ROOT / ('result-method-b-%s.json' % args.model.replace(':', '_')))
    out.write_text(json.dumps({'arm': 'B', 'model': args.model, 'samplesPerPrompt': args.samples,
                               'corpus': str(CORPUS.name), 'rows': rows}, indent=1), encoding='utf-8')
    valid = sum(1 for r in rows if r.get('ok'))
    print('\n%d/%d valid SVG documents; wrote %s' % (valid, len(rows), out.name))
    return 0


if __name__ == '__main__':
    sys.exit(main())
