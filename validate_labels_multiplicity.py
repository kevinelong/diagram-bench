"""validate_labels_multiplicity.py - catches duplicated or missing labels, which "at least N" checks cannot.

For every prompt with a `label_multiplicity` in structure-expectations.json, compare the multiset of text nodes in
the SVG against the spec. This is the check that sees `BATH` written twice in a two-bedroom plan.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
SVG = '{http://www.w3.org/2000/svg}'


def texts(svg_text):
    root = ET.fromstring(svg_text)
    out = []
    for el in root.iter():
        if el.tag == SVG + 'text':
            t = ' '.join(el.itertext()).strip()
            if t:
                out.append(t)
    return out


def main(model='qwen3-coder:30b', src=None):
    expect = json.loads((ROOT / 'structure-expectations.json').read_text(encoding='utf-8'))['expect']
    src = Path(src) if src else (ROOT / 'svg' / model.replace(':', '_'))
    rows = []
    for pid, spec in expect.items():
        want = spec.get('label_multiplicity')
        if not want:
            continue
        for svg in sorted(src.glob(pid + '-s*.svg')):
            found = texts(svg.read_text(encoding='utf-8'))
            # count each expected name as a whole word, case-insensitive
            got = {name: sum(1 for t in found if re.search(r'\b' + re.escape(name) + r'\b', t, re.I))
                   for name in want}
            ok = all(got[k] == v for k, v in want.items())
            rows.append({'sample': svg.stem, 'expected': want, 'found': got, 'pass': ok,
                         'all_texts': found})
            print('  %-16s %s  %s' % (svg.stem, got, 'ok' if ok else 'MISMATCH: expected %s' % want))
    passed = sum(1 for r in rows if r['pass'])
    (ROOT / 'result-method-b-multiplicity.json').write_text(
        json.dumps({'arm': 'B-multiplicity', 'model': model, 'passed': passed, 'documents': len(rows), 'rows': rows},
                   indent=1), encoding='utf-8')
    print('\nlabel multiset correct: %d/%d' % (passed, len(rows)))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'qwen3-coder:30b')
