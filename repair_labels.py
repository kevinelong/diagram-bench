"""repair_labels.py - the cheapest improvement in the image track: validate labels, then ask for a repair.

Method B produces SVG source, so its labels are checkable exactly rather than by OCR: every required string
must exist inside a <text> element. This script measures that, re-asks the model for a corrected document when a
label is missing, and records before/after. No image model, no GPU.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe repair_labels.py --model qwen3-coder:30b
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
SVG_NS = '{http://www.w3.org/2000/svg}'

SYSTEM = ('You are a technical illustrator. You output ONE complete SVG document and nothing else: no prose, '
          'no explanation, no markdown fences.')

REPAIR_RULES = ('\n\nRules: viewBox="0 0 1024 1024", width and height 1024; <rect>, <line>, <path>, <circle> and '
                '<text> only; every label must be a real <text> element containing exactly the specified string, '
                'font-family="Arial", font-size 28 or larger; black strokes and flat fills on white; no gradients, '
                'filters, shadows, images, external fonts or <style> blocks; keep labels inside their shapes.')


def texts(svg_text):
    try:
        root = ET.fromstring(svg_text)
    except Exception:
        return []
    out = []
    for el in root.iter():
        if el.tag in (SVG_NS + 'text', 'text') and el.text:
            out.append(' '.join(el.itertext()).strip())
    return out


def missing(labels, found):
    hay = ' | '.join(t.lower() for t in found)
    return [l for l in labels if l.lower() not in hay]


def ask(model, prompt, timeout=600):
    body = {'model': model, 'system': SYSTEM, 'prompt': prompt, 'stream': False,
            'options': {'temperature': 0, 'seed': 4242, 'num_predict': 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    return payload.get('response', ''), round(time.monotonic() - started, 1)


def extract_svg(text):
    m = re.search(r'<svg[\s\S]*?</svg>', text, re.IGNORECASE)
    return m.group(0) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--dir', default=None)
    args = ap.parse_args()

    corpus = json.loads(CORPUS.read_text(encoding='utf-8'))
    by_id = {p['id']: p for p in corpus['prompts']}
    src_dir = Path(args.dir) if args.dir else (ROOT / 'svg' / args.model.replace(':', '_'))
    out_dir = ROOT / 'svg' / (args.model.replace(':', '_') + '-repaired')
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for svg_path in sorted(src_dir.glob('*.svg')):
        pid = svg_path.stem.rsplit('-s', 1)[0]
        prompt = by_id.get(pid)
        if not prompt:
            continue
        labels = prompt.get('labels', [])
        source = svg_path.read_text(encoding='utf-8')
        found = texts(source)
        miss = missing(labels, found)
        row = {'id': pid, 'sample': svg_path.stem, 'labels': labels, 'found_before': found,
               'missing_before': miss, 'repaired': False}
        if miss:
            instruction = ('This SVG is missing these required labels: %s. Return the complete corrected SVG '
                           'so that every one of these labels appears verbatim inside its own <text> element, '
                           'keeping everything else that already works. Original SVG follows.\n\n%s'
                           % (', '.join(miss), source))
            try:
                reply, seconds = ask(args.model, instruction + REPAIR_RULES)
                fixed = extract_svg(reply)
                if fixed:
                    try:
                        ET.fromstring(fixed)
                    except Exception as e:
                        fixed = None
                        row['error'] = 'repaired SVG did not parse: %s' % e
                if fixed:
                    (out_dir / svg_path.name).write_text(fixed, encoding='utf-8')
                    found_after = texts(fixed)
                    row.update({'repaired': True, 'repair_s': seconds, 'found_after': found_after,
                                'missing_after': missing(labels, found_after)})
            except Exception as e:
                row['error'] = type(e).__name__ + ': ' + str(e)
        row['labels_ok_before'] = len(labels) - len(miss)
        row['labels_ok_after'] = (len(labels) - len(row.get('missing_after', miss)))
        rows.append(row)
        print('  %-16s labels %d/%d -> %d/%d  %s'
              % (svg_path.stem, row['labels_ok_before'], len(labels), row['labels_ok_after'], len(labels),
                 'repaired' if row['repaired'] else ('ok' if not miss else 'repair failed')), flush=True)

    before = sum(r['labels_ok_before'] for r in rows)
    after = sum(r['labels_ok_after'] for r in rows)
    total = sum(len(r['labels']) for r in rows)
    payload = {'arm': 'B-repair', 'model': args.model, 'documents': len(rows),
               'labels_before': before, 'labels_after': after, 'labels_total': total,
               'documents_missing_a_label': sum(1 for r in rows if r['missing_before']),
               'documents_full_after': sum(1 for r in rows if r['labels_ok_after'] == len(r['labels'])),
               'rows': rows}
    out = ROOT / 'result-method-b-repair.json'
    out.write_text(json.dumps(payload, indent=1), encoding='utf-8')
    print('\nlabels present in source: %d/%d -> %d/%d  (documents fully labelled: %d/%d)'
          % (before, total, after, total, payload['documents_full_after'], len(rows)))


if __name__ == '__main__':
    sys.exit(main())
