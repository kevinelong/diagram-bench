"""validate_structure.py - deterministic structural checks on method B's SVG source, with a repair round.

Method B emits SVG, so its structure is countable: rectangles, connectors, text nodes. That is a far better
instrument than asking a vision model whether "there are exactly four boxes", and it costs nothing.

Checks, per document, against `structure-expectations.json` (frozen before this ran):
  boxes      = <rect> count that is not a full-canvas background
  connectors = <line> count plus <path> elements with 2 or fewer curve commands
  labels     = <text> elements that contain non-empty text

A mismatch triggers one repair round asking the model to fix only the counts, then re-validates.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe validate_structure.py --model qwen3-coder:30b [--no-repair]
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

RULES = ('\n\nRules: viewBox="0 0 1024 1024"; <rect>, <line>, <path>, <circle>, <text> only; black strokes, flat '
         'fills, white background; no gradients, filters, shadows, images, external fonts or <style> blocks; every '
         'label is a real <text> element with font-family="Arial" and font-size 28 or larger.')


def counts(svg_text):
    try:
        root = ET.fromstring(svg_text)
    except Exception as e:
        return None, 'parse error: %s' % e
    rects = connectors = labels = 0
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        if tag == 'rect':
            try:
                w = float(el.get('width', 0)); h = float(el.get('height', 0))
            except Exception:
                w = h = 0
            if w < 900 or h < 900:          # ignore a full-canvas background rect
                rects += 1
        elif tag == 'line':
            connectors += 1
        elif tag == 'path':
            d = el.get('d', '')
            if d.count('C') + d.count('Q') <= 2:
                connectors += 1
        elif tag == 'text':
            if ' '.join(el.itertext()).strip():
                labels += 1
    return {'boxes': rects, 'connectors': connectors, 'labels': labels}, None


def check(found, expect):
    """v2 expectations: <field>_exact or <field>_at_least; anything unmentioned is measured, not enforced."""
    problems = []
    for k in ('boxes', 'connectors', 'labels'):
        exact = expect.get(k + '_exact')
        least = expect.get(k + '_at_least')
        if exact is not None and found[k] != exact:
            problems.append('%s: expected exactly %d, found %d' % (k, exact, found[k]))
        elif least is not None and found[k] < least:
            problems.append('%s: expected at least %d, found %d' % (k, least, found[k]))
    return problems


def ask(model, prompt, timeout=600):
    body = {'model': model, 'system': SYSTEM, 'prompt': prompt, 'stream': False,
            'options': {'temperature': 0, 'seed': 777, 'num_predict': 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    return payload.get('response', ''), round(time.monotonic() - started, 1)


def extract(svg_text):
    m = re.search(r'<svg[\s\S]*?</svg>', svg_text, re.IGNORECASE)
    return m.group(0) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--dir', default=None)
    ap.add_argument('--no-repair', action='store_true')
    args = ap.parse_args()

    expect = json.loads((ROOT / 'structure-expectations.json').read_text(encoding='utf-8'))['expect']
    src = Path(args.dir) if args.dir else (ROOT / 'svg' / args.model.replace(':', '_'))
    outdir = ROOT / 'svg' / (args.model.replace(':', '_') + '-struct')
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    for svg_path in sorted(src.glob('*.svg')):
        pid = svg_path.stem.rsplit('-s', 1)[0]
        if pid not in expect:
            continue
        text = svg_path.read_text(encoding='utf-8')
        found, err = counts(text)
        if found is None:
            rows.append({'id': pid, 'sample': svg_path.stem, 'error': err})
            print('  %-16s PARSE ERROR' % svg_path.stem)
            continue
        problems = check(found, expect[pid])
        row = {'id': pid, 'sample': svg_path.stem, 'before': found, 'problems_before': problems}
        if problems and not args.no_repair:
            instruction = ('Your SVG for this diagram does not match the required structure. Problems: %s.\n'
                           'Return the complete corrected SVG with exactly the right counts, keeping the existing '
                           'labels and layout where they already satisfy the requirement.\n\n%s'
                           % ('; '.join(problems), text))
            try:
                reply, seconds = ask(args.model, instruction + RULES)
                fixed = extract(reply)
                if fixed:
                    after, err2 = counts(fixed)
                    if after:
                        (outdir / svg_path.name).write_text(fixed, encoding='utf-8')
                        row.update({'repaired': True, 'repair_s': seconds, 'after': after,
                                    'problems_after': check(after, expect[pid])})
            except Exception as e:
                row['repair_error'] = type(e).__name__ + ': ' + str(e)
        rows.append(row)
        print('  %-16s boxes %-4s conn %-4s labels %-4s  %s' % (
            svg_path.stem, found['boxes'], found['connectors'], found['labels'],
            'ok' if not problems else ('repaired' if row.get('repaired') else 'FAIL: ' + '; '.join(problems))),
            flush=True)

    def passed(r, key):
        return bool(r.get(key)) and not r.get(key + '_problems' if key == 'before' else 'problems_after')

    clean_before = sum(1 for r in rows if r.get('problems_before') == [])
    clean_after = sum(1 for r in rows if ('problems_after' in r and r['problems_after'] == [])
                      or ('problems_after' not in r and r.get('problems_before') == []))
    payload = {'arm': 'B-structure', 'model': args.model, 'documents': len(rows),
               'structurally_correct_before': clean_before, 'structurally_correct_after': clean_after,
               'rows': rows}
    (ROOT / 'result-method-b-structure.json').write_text(json.dumps(payload, indent=1), encoding='utf-8')
    print('\nstructurally correct: %d/%d -> %d/%d' % (clean_before, len(rows), clean_after, len(rows)))


if __name__ == '__main__':
    sys.exit(main())
