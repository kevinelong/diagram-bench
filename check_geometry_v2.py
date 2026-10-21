"""check_geometry_v2.py - the four geometric checks the v1 test lacked.

v1 counted circles and passed a table whose side pockets were on the short rails. These checks locate things:

  pockets      corners vs mid-long-rail vs short-rail, by position
  rack apex    leftmost interior ball, on the centre line
  back row     the five rightmost balls sharing an x (parallel to the foot rail)
  cue ball     leftmost interior ball at the head quarter, on the centre line
  cue angle    20-45 degrees, counting a rect rotated by transform

Works with or without <rect> for the table: if no rect, the table is the bounding box of the axis-aligned lines.

Usage (cmd.exe):  .venv\\Scripts\\python.exe check_geometry_v2.py <file.svg>
"""
import json
import math
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


def table_box(root):
    W = f(root, 'width', 1024) or 1024
    H = f(root, 'height', 1024) or 1024
    allr = [(f(r, 'x'), f(r, 'y'), f(r, 'width'), f(r, 'height')) for r in root.iter(SVG + 'rect')]
    allr = [r for r in allr if r[2] > 50 and r[3] > 50]
    big = [r for r in allr if r[2] >= 0.98 * W and r[3] >= 0.98 * H]
    # a rect covering the whole viewBox is the table itself when it is the only candidate
    rects = [r for r in allr if r not in big] or big
    if rects:
        return max(rects, key=lambda r: r[2] * r[3])
    xs, ys = [], []
    for ln in root.iter(SVG + 'line'):
        x1, y1, x2, y2 = f(ln, 'x1'), f(ln, 'y1'), f(ln, 'x2'), f(ln, 'y2')
        if abs(y1 - y2) <= 3 or abs(x1 - x2) <= 3:      # axis-aligned edges only
            xs += [x1, x2]
            ys += [y1, y2]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def cue_angle(root):
    best = None
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        if tag not in ('rect', 'line'):
            continue
        rot = el.get('transform') or ''
        m = re.search(r'rotate\(\s*(-?[\d.]+)', rot)
        rot_deg = float(m.group(1)) if m else 0.0
        if tag == 'rect':
            w, h = f(el, 'width'), f(el, 'height')
            if w >= 80 and h <= 24:
                cand = (rot_deg, w)
            else:
                continue
        else:
            x1, y1, x2, y2 = f(el, 'x1'), f(el, 'y1'), f(el, 'x2'), f(el, 'y2')
            length = math.hypot(x2 - x1, y2 - y1)
            if length < 80:
                continue
            cand = (math.degrees(math.atan2(y2 - y1, x2 - x1)) + rot_deg, length)
        # a line drawn tip->butt reads ~180 degrees from its axis: normalise to the axis angle
        axis = abs(((cand[0] + 90) % 180) - 90)
        off_axis = min(axis, 90 - axis)
        if off_axis > 2 and (best is None or cand[1] > best[1]):
            best = (axis, cand[1])
    return None if best is None else round(best[0], 1)


def analyse(path):
    root = ET.fromstring(Path(path).read_text(encoding='utf-8'))
    box = table_box(root)
    res = {'file': Path(path).name}
    if not box:
        res['error'] = 'no table found'
        return res
    tx, ty, tw, th = box
    cx_t, cy_t = tx + tw / 2, ty + th / 2
    res['table'] = [round(v, 1) for v in box]
    aspect = round(tw / th, 2)
    tol = 0.05 * min(tw, th)

    circles = [(f(c, 'cx'), f(c, 'cy'), f(c, 'r')) for c in root.iter(SVG + 'circle')]
    circles = [c for c in circles if 4 <= c[2] <= 40]
    corners = long_rail = short_rail = 0
    interior = []
    for x, y, r in circles:
        near_l = abs(x - tx) <= tol
        near_r = abs(x - (tx + tw)) <= tol
        near_t = abs(y - ty) <= tol
        near_b = abs(y - (ty + th)) <= tol
        on_edge = near_l or near_r or near_t or near_b
        if not on_edge:
            interior.append((x, y, r))
            continue
        cornerish = (near_t or near_b) and (abs(x - tx) < 0.18 * tw or abs(x - (tx + tw)) < 0.18 * tw)
        if cornerish:
            corners += 1
        elif near_t or near_b:
            long_rail += 1
        else:
            short_rail += 1

    interior.sort()
    cue_ball = interior[0] if interior else None
    rack = interior[1:] if interior else []
    apex_ok = False
    back_ok = False
    if rack:
        leftmost = min(rack, key=lambda b: b[0])
        apex_ok = abs(leftmost[1] - cy_t) <= 0.06 * th
        right_five = sorted(rack, key=lambda b: -b[0])[:5]
        if len(right_five) == 5:
            xs = [b[0] for b in right_five]
            ball_d = 2 * max(b[2] for b in rack)
            back_ok = (max(xs) - min(xs)) <= ball_d
    cue_ok = bool(cue_ball) and abs(cue_ball[1] - cy_t) <= 0.06 * th and \
        abs(cue_ball[0] - (tx + 0.25 * tw)) <= 0.10 * tw
    ang = cue_angle(root)
    texts = [' '.join(t.itertext()).upper() for t in root.iter(SVG + 'text')]

    res.update({
        'aspect': aspect,
        'corners': corners, 'long_rail_side_pockets': long_rail, 'short_rail_side_pockets': short_rail,
        'interior_balls': len(interior),
        'apex_on_centre': apex_ok, 'back_row_parallel': back_ok,
        'cue_ball_on_spot': cue_ok, 'cue_ball': None if not cue_ball else [round(cue_ball[0], 1), round(cue_ball[1], 1)],
        'cue_deg': ang, 'label_CUE': any('CUE' in t for t in texts),
    })
    res['pass'] = {
        'table_2to1': 1.8 <= aspect <= 2.2,
        'pockets_long_rail': (long_rail == 2 and corners == 4 and short_rail == 0),
        'rack_apex': apex_ok,
        'rack_back_row': back_ok,
        'cue_ball_spot': cue_ok,
        'cue_angle': ang is not None and 20 <= abs(ang) <= 45,
        'label_CUE': res['label_CUE'],
    }
    res['score'] = '%d/%d' % (sum(res['pass'].values()), len(res['pass']))
    return res


if __name__ == '__main__':
    for p in sys.argv[1:]:
        r = analyse(p)
        print(json.dumps(r, indent=1))
