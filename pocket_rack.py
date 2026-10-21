"""pocket_rack.py - where are the pockets, and which way does the rack point?

Left to right, the table's long axis is horizontal, so the LONG rails are top and bottom and the SHORT rails are
left and right. A pool table has corner pockets and side pockets at the MIDPOINT OF THE LONG RAILS. A rack has its
head ball on the foot spot with the flat back parallel to the foot short rail.
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG = '{http://www.w3.org/2000/svg}'
f = lambda el, a, d=0.0: float(str(el.get(a, d)).replace('px', ''))


def analyse(path):
    root = ET.fromstring(Path(path).read_text(encoding='utf-8'))
    W, H = f(root, 'width', 1024) or 1024, f(root, 'height', 1024) or 1024
    rects = [(f(r, 'x'), f(r, 'y'), f(r, 'width'), f(r, 'height')) for r in root.iter(SVG + 'rect')]
    rects = [r for r in rects if not (r[2] >= 0.9 * W and r[3] >= 0.9 * H) and r[2] > 50 and r[3] > 50]
    tx, ty, tw, th = max(rects, key=lambda r: r[2] * r[3])
    circles = [(f(c, 'cx'), f(c, 'cy'), f(c, 'r')) for c in root.iter(SVG + 'circle')]
    balls = [c for c in circles if 6 <= c[2] <= 30]
    pockets, rest = [], []
    tol = 0.06 * min(tw, th)
    for cx, cy, r in balls:
        on_left = abs(cx - tx) <= tol
        on_right = abs(cx - (tx + tw)) <= tol
        on_top = abs(cy - ty) <= tol
        on_bottom = abs(cy - (ty + th)) <= tol
        if on_left or on_right:
            rest.append(('short-rail', cx, cy))
        elif on_top or on_bottom:
            rest.append(('long-rail', cx, cy))
        else:
            pockets.append((cx, cy, r))
    # classify edge circles: near a corner = corner pocket, else mid
    long_side, short_side, corners = 0, 0, 0
    for where, cx, cy in rest:
        near_corner = (abs(cx - tx) < 0.2 * tw or abs(cx - (tx + tw)) < 0.2 * tw) and \
                      (abs(cy - ty) < 0.2 * th or abs(cy - (ty + th)) < 0.2 * th)
        if where == 'long-rail':
            corners += 1 if near_corner else 0
            long_side += 0 if near_corner else 1
        else:
            corners += 1 if near_corner else 0
            short_side += 0 if near_corner else 1
    # rack orientation: ball cluster away from the cue ball
    rack = pockets if pockets else balls
    xs = sorted(b[0] for b in rack)
    if len(xs) >= 6:
        left_half = [b for b in rack if b[0] < (xs[0] + xs[-1]) / 2]
        right_half = [b for b in rack if b[0] >= (xs[0] + xs[-1]) / 2]
        apex = 'left' if len(left_half) < len(right_half) else 'right'
        widths = (len(left_half), len(right_half))
    else:
        apex, widths = None, None
    return {'table': [round(v, 1) for v in (tx, ty, tw, th)],
            'circles_total': len(balls), 'interior_circles': len(pockets),
            'corner_pockets': corners, 'side_pockets_long_rail': long_side,
            'pockets_on_short_rail': short_side,
            'rack_apex_points': apex, 'rack_half_counts': widths}


for path in sys.argv[1:]:
    try:
        print(Path(path).name)
        for k, v in analyse(path).items():
            print('   %-24s %s' % (k, v))
    except Exception as e:
        print(Path(path).name, '-> error', e)
