"""svg_morph.py - morph between two SVGs that share element structure.

Morphing arbitrary diagrams is genuinely hard: you need a correspondence between their parts, and if one has five boxes
and the other seven there is nothing to interpolate. This module does the honest, bounded version - it requires the two
documents to have the same number of rect/line/text elements, then interpolates their geometry:

    rect  x, y, width, height
    line  x1, y1, x2, y2
    text  x, y  (and, when the words differ, a crossfade - glyphs cannot be interpolated)

The intended input is two samples of the same prompt, e.g. the local model's flow-1-s0 and flow-1-s1: same diagram,
different layout, so the correspondence is positional by construction.

    python svg_morph.py a.svg b.svg out/morph 48
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

SVG = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG)
TAGS = ('rect', 'line', 'text')
NUMERIC = {'rect': ('x', 'y', 'width', 'height'),
           'line': ('x1', 'y1', 'x2', 'y2'),
           'text': ('x', 'y')}


def ease(p):
    p = max(0.0, min(1.0, p))
    return p * p * (3 - 2 * p)


def parts(root):
    out = {}
    for tag in TAGS:
        els = [e for e in root.iter('{%s}%s' % (SVG, tag))]
        if tag == 'rect':
            els = [e for e in els if not (e.get('width') == '1024' and e.get('height') == '1024')]
        out[tag] = els
    return out


def check(pa, pb, na, nb):
    for tag in TAGS:
        if len(pa[tag]) != len(pb[tag]):
            raise SystemExit('%s: unmatched structure %s %d vs %d - move the boxes so the counts agree'
                             % (tag, na, nb, len(pa[tag]), len(pb[tag])))


def frame(path_a, path_b, t, W=900, H=900):
    ra = ET.parse(path_a).getroot()
    rb = ET.parse(path_b).getroot()
    pa, pb = parts(ra), parts(rb)
    check(pa, pb, path_a, path_b)
    ra.set('width', str(W))
    ra.set('height', str(H))
    ra.set('preserveAspectRatio', 'xMidYMid meet')
    e = ease(t)

    for tag in TAGS:
        for ea, eb in zip(pa[tag], pb[tag]):
            for attr in NUMERIC[tag]:
                va, vb = ea.get(attr), eb.get(attr)
                if va is None or vb is None:
                    continue
                try:
                    ea.set(attr, '%.2f' % (float(va) + (float(vb) - float(va)) * e))
                except ValueError:
                    continue
            if tag == 'text':
                ta, tb = (ea.text or ''), (eb.text or '')
                if ta.strip() != tb.strip():
                    # different words: crossfade in place instead of pretending glyphs move
                    ea.set('opacity', '%.3f' % max(0.0, 1 - 2 * e))
                    clone = deepcopy(eb)
                    clone.set('opacity', '%.3f' % max(0.0, 2 * e - 1))
                    ra.append(clone)
    return ET.tostring(ra, encoding='utf-8')


def main(argv):
    if len(argv) < 4:
        raise SystemExit(__doc__)
    a, b, outdir = Path(argv[0]), Path(argv[1]), Path(argv[2])
    n = int(argv[3]) if len(argv) > 3 else 48
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob('*.svg'):
        f.unlink()
    for i in range(n):
        (outdir / ('%04d.svg' % (i + 1))).write_bytes(frame(a, b, i / (n - 1)))
    print('morph: %d frames, %s -> %s' % (n, a.name, b.name))


if __name__ == '__main__':
    main(sys.argv[1:])
