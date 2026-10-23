"""svg_motion.py - animate the *elements* of our own SVGs into short vector clips.

Two effects, both operating on structure rather than pixels, so the output stays crisp:

  assemble - boxes slide up and fade in one after another, arrows draw themselves (stroke-dashoffset), labels fade in
  reveal   - nothing moves; every stroke draws itself in sequence, then the text fades in

Frames are written as SVG files, one per frame, then rasterised (Chromium) and encoded (ffmpeg). Element identity comes
from the document order of rect/line/text, which is stable because we generate these SVGs ourselves.

    python svg_motion.py assemble ../grounded_ref/flow-1.svg out/assemble 48
    python svg_motion.py reveal   ../grounded_ref/flow-1.svg out/reveal   48
"""
from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG)


def ease(p):
    p = max(0.0, min(1.0, p))
    return p * p * (3 - 2 * p)


def span(t, start, dur):
    return ease((t - start) / dur)


def load(path):
    tree = ET.parse(path)
    root = tree.getroot()
    rects = [e for e in root.iter('{%s}rect' % SVG)]
    lines = [e for e in root.iter('{%s}line' % SVG)]
    texts = [e for e in root.iter('{%s}text' % SVG)]
    # the full-canvas background rect is not an element to animate
    boxes = [r for r in rects if not (r.get('width') == '1024' and r.get('height') == '1024')]
    marks = [ln.get('marker-end') for ln in lines]
    return tree, root, boxes, lines, texts, marks


def line_len(e):
    return math.hypot(float(e.get('x2')) - float(e.get('x1')), float(e.get('y2')) - float(e.get('y1')))


def rect_len(e):
    return 2 * (float(e.get('width')) + float(e.get('height')))


def render_frame(path, out, effect, t, W=900, H=900):
    tree, root, boxes, lines, texts, marks = load(path)
    root.set('width', str(W))
    root.set('height', str(H))
    root.set('preserveAspectRatio', 'xMidYMid meet')

    if effect == 'assemble':
        for i, r in enumerate(boxes):
            p = span(t, 0.06 + 0.12 * i, 0.34)
            r.set('y', '%.2f' % (float(r.get('y')) + (1 - p) * 70))
            r.set('opacity', '%.3f' % p)
        for j, ln in enumerate(lines):
            p = span(t, 0.30 + 0.12 * j, 0.30)
            L = line_len(ln)
            ln.set('stroke-dasharray', '%.2f' % L)
            ln.set('stroke-dashoffset', '%.2f' % (L * (1 - p)))
            if p > 0.02 and marks[j]:
                ln.set('marker-end', marks[j])
            else:
                ln.attrib.pop('marker-end', None)
        for k, tx in enumerate(texts):
            p = span(t, 0.42 + 0.10 * k, 0.30)
            tx.set('opacity', '%.3f' % p)
    elif effect == 'reveal':
        order = [(r, rect_len(r)) for r in boxes] + [(l, line_len(l)) for l in lines]
        step = 0.55 / max(len(order), 1)
        for i, (el, L) in enumerate(order):
            p = span(t, 0.04 + step * i, 0.30)
            el.set('stroke-dasharray', '%.2f' % L)
            el.set('stroke-dashoffset', '%.2f' % (L * (1 - p)))
            if el.tag.endswith('line'):
                if p > 0.02:
                    m = marks[[l for l in lines].index(el)]
                    if m: el.set('marker-end', m)
                else:
                    el.attrib.pop('marker-end', None)
        for k, tx in enumerate(texts):
            p = span(t, 0.60 + 0.06 * k, 0.28)
            tx.set('opacity', '%.3f' % p)
    else:
        raise SystemExit('effect must be assemble or reveal')

    out.write_bytes(ET.tostring(root, encoding='utf-8', xml_declaration=False))


def main(argv):
    if len(argv) < 4:
        raise SystemExit(__doc__)
    effect, src, outdir = argv[0], Path(argv[1]), Path(argv[2])
    n = int(argv[3]) if len(argv) > 3 else 48
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob('*.svg'):
        f.unlink()
    for i in range(n):
        render_frame(src, outdir / ('%04d.svg' % (i + 1)), effect, i / (n - 1))
    print('%s: %d frames -> %s' % (effect, n, outdir))


if __name__ == '__main__':
    main(sys.argv[1:])
