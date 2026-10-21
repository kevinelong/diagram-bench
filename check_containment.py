"""check_containment.py - are the balls inside the table?

A pool diagram can satisfy every counting and label check and still be wrong in the way that matters: the rack
placed outside the cushions. This measures containment directly, from either artifact:

  SVG (method B): largest non-canvas <rect> is the table; circles with 6 <= r <= 30 are balls
  .kicad_pcb (method E): gr_rect start/end is the table; gr_circle centre+radius are balls/pockets

Usage (cmd.exe):  .venv\\Scripts\\python.exe check_containment.py <file.svg|file.kicad_pcb>
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG = '{http://www.w3.org/2000/svg}'


def f(el, a, d=0.0):
    try:
        return float(str(el.get(a, d)).replace('px', ''))
    except Exception:
        return d


def from_svg(path):
    root = ET.fromstring(Path(path).read_text(encoding='utf-8'))
    W, H = f(root, 'width', 1024) or 1024, f(root, 'height', 1024) or 1024
    rects = [(f(r, 'x'), f(r, 'y'), f(r, 'width'), f(r, 'height'))
             for r in root.iter(SVG + 'rect')]
    rects = [r for r in rects if not (r[2] >= 0.9 * W and r[3] >= 0.9 * H) and r[2] > 50 and r[3] > 50]
    if not rects:
        return None
    tx, ty, tw, th = max(rects, key=lambda r: r[2] * r[3])
    balls = [(f(c, 'cx'), f(c, 'cy'), f(c, 'r')) for c in root.iter(SVG + 'circle')]
    balls = [b for b in balls if 6 <= b[2] <= 30]
    inside = [b for b in balls if tx <= b[0] <= tx + tw and ty <= b[1] <= ty + th]
    return {'table': [tx, ty, tw, th], 'balls': len(balls), 'inside': len(inside),
            'outside': len(balls) - len(inside),
            'aspect': round(tw / th, 2) if th else None}


def blocks(text, tag):
    """extract balanced (tag ...) blocks - nested parentheses make regex the wrong tool."""
    out, i = [], 0
    needle = '(' + tag
    while True:
        i = text.find(needle, i)
        if i < 0:
            return out
        depth, j = 0, i
        while j < len(text):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(text[i:j + 1])
        i = j + 1


def from_pcb(path):
    """KiCad 10 writes .kicad_pcb coordinates in MILLIMETRES, and the S-expressions span lines."""
    text = Path(path).read_text(encoding='utf-8')
    table = None
    for b in blocks(text, 'gr_rect'):
        if 'F.SilkS' not in b:
            continue
        s = re.search(r'\(start (-?[\d.]+) (-?[\d.]+)\)', b)
        e = re.search(r'\(end (-?[\d.]+) (-?[\d.]+)\)', b)
        if s and e:
            x0, y0 = float(s.group(1)), float(s.group(2))
            x1, y1 = float(e.group(1)), float(e.group(2))
            table = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            break
    if table is None:
        return None
    tx, ty, tw, th = table
    balls = []
    for b in blocks(text, 'gr_circle'):
        if 'F.SilkS' not in b:
            continue
        c = re.search(r'\(center (-?[\d.]+) (-?[\d.]+)\)', b)
        e = re.search(r'\(end (-?[\d.]+) (-?[\d.]+)\)', b)
        if c and e:
            cx, cy = float(c.group(1)), float(c.group(2))
            r = ((float(e.group(1)) - cx) ** 2 + (float(e.group(2)) - cy) ** 2) ** 0.5
            balls.append((cx, cy, r))
    inside = [b for b in balls if tx <= b[0] <= tx + tw and ty <= b[1] <= ty + th]
    return {'table_mm': [round(v, 1) for v in (tx, ty, tw, th)], 'circles': len(balls),
            'inside': len(inside), 'outside': len(balls) - len(inside),
            'aspect': round(tw / th, 2) if th else None,
            'outside_centres': [(round(b[0], 1), round(b[1], 1)) for b in balls if b not in inside][:6]}


if __name__ == '__main__':
    path = sys.argv[1]
    result = from_pcb(path) if path.endswith('.kicad_pcb') else from_svg(path)
    print(Path(path).name, '->', result)
