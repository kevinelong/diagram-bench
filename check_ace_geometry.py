"""check_ace_geometry.py - assert the ACE constants directly, in ACE's own coordinate system.

The v3 prompt asks for viewBox="-8 -8 116 66" and the app's constants (POCKET_ZONES, BALL_RADIUS 1.125, diamonds at
eighths with 50 omitted, head spot (25,25), foot spot (75,25), rack rows spaced BALL_D*sin60 = 1.949). Those are
numbers, so the check is an assertion rather than an opinion.

Usage (cmd.exe):  .venv\\Scripts\\python.exe check_ace_geometry.py <file.svg>
"""
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG = '{http://www.w3.org/2000/svg}'
CORNERS = [(1.5, 1.5), (98.5, 1.5), (1.5, 48.5), (98.5, 48.5)]
SIDES = [(50.0, 1.0), (50.0, 49.0)]
LONG_DIAMONDS = [12.5, 25.0, 37.5, 62.5, 75.0, 87.5]
BALL_R = 1.125
ROW_SPACING = BALL_R * 2 * math.sin(math.pi / 3)   # 1.949


def n(el, a, d=0.0):
    try:
        return float(str(el.get(a, d)).replace('px', ''))
    except Exception:
        return d


def near(a, b, tol):
    return abs(a - b) <= tol


def rot_of(el):
    m = re.search(r'rotate\(\s*(-?[\d.]+)', el.get('transform') or '')
    return float(m.group(1)) if m else 0.0


def walk(el, acc_rot=0.0):
    """yield (element, effective rotation) honouring inherited group transforms."""
    here = acc_rot + rot_of(el)
    yield el, here
    for child in el:
        yield from walk(child, here)


def analyse(path):
    root = ET.fromstring(Path(path).read_text(encoding='utf-8'))
    eff = {id(el): rot for el, rot in walk(root)}
    circles = [(n(c, 'cx'), n(c, 'cy'), n(c, 'r')) for c in root.iter(SVG + 'circle')]
    rects = [(n(r, 'x'), n(r, 'y'), n(r, 'width'), n(r, 'height')) for r in root.iter(SVG + 'rect')]
    polys = [p.get('points', '') for p in root.iter(SVG + 'polygon')]
    texts = [' '.join(t.itertext()).strip() for t in root.iter(SVG + 'text')]

    play = [r for r in rects if near(r[2] / r[3], 2.0, 0.06) and r[2] > 30] if rects else []
    play = max(play, key=lambda r: r[2] * r[3]) if play else None

    # pocket-sized circles on or near the cloth boundary
    def pocket_at(target, tol=1.5):
        return [c for c in circles if near(c[0], target[0], tol) and near(c[1], target[1], tol)]

    corners_hit = sum(1 for t in CORNERS if pocket_at(t))
    sides_hit = sum(1 for t in SIDES if pocket_at(t))

    # balls: small circles, i.e. radius within 40% of BALL_R
    balls = [c for c in circles if near(c[2], BALL_R, BALL_R * 0.45)]
    inside = [b for b in balls if 0 + b[2] <= b[0] <= 100 - b[2] and 0 + b[2] <= b[1] <= 50 - b[2]]

    # diamond markers as polygons, by centroid
    def centroid(points):
        nums = [float(v) for v in re.findall(r'-?[\d.]+', points)]
        xs, ys = nums[0::2], nums[1::2]
        return (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else (None, None)

    cents = [centroid(p) for p in polys]
    for r in root.iter(SVG + 'rect'):
        w, h = n(r, 'width'), n(r, 'height')
        if 0.2 <= w <= 1.2 and 0.2 <= h <= 1.2 and abs(eff.get(id(r), 0.0) % 90) >= 30:
            cents.append((n(r, 'x') + w / 2, n(r, 'y') + h / 2))
    long_diamonds = set()
    for x, y in cents:
        if x is None:
            continue
        for d in LONG_DIAMONDS:
            if near(x, d, 1.0) and (near(y, -3, 2.0) or near(y, 53, 2.0)):
                long_diamonds.add(d)
    at_fifty = any(x is not None and near(x, 50, 1.0) and (near(y, -3, 2.0) or near(y, 53, 2.0)) for x, y in cents)

    # rack: balls away from the head spot, apex nearest the foot spot
    rack = [b for b in balls if b[0] > 40]
    apex = min(rack, key=lambda b: abs(b[0] - 75) + abs(b[1] - 25)) if rack else None
    apex_ok = bool(apex) and near(apex[0], 75, 1.5) and near(apex[1], 25, 1.5)
    rows = sorted({round(b[0] / ROW_SPACING) for b in rack}) if rack else []
    back = sorted(rack, key=lambda b: -b[0])[:5]
    back_ok = len(back) == 5 and (max(b[0] for b in back) - min(b[0] for b in back)) <= 2.2 * BALL_R

    # cue: a long thin element, angle by axis
    cue = None
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        rot_deg = eff.get(id(el), 0.0)
        if tag == 'rect':
            w, h = n(el, 'width'), n(el, 'height')
            if w >= 15 and h <= 2:
                a = rot_deg
                L = w
            else:
                continue
        elif tag == 'line':
            x1, y1, x2, y2 = n(el, 'x1'), n(el, 'y1'), n(el, 'x2'), n(el, 'y2')
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 15:
                continue
            a = math.degrees(math.atan2(y2 - y1, x2 - x1)) + rot_deg
        else:
            continue
        axis = abs(((a + 90) % 180) - 90)
        if cue is None or L > cue[1]:
            cue = (axis, L)

    res = {
        'file': Path(path).name,
        'playfield': None if not play else [round(v, 1) for v in play],
        'corners_hit': corners_hit, 'sides_hit': sides_hit,
        'balls': len(balls), 'balls_inside': len(inside),
        'long_diamonds_found': sorted(long_diamonds), 'diamond_at_50': at_fifty,
        'apex_on_foot_spot': apex_ok, 'rack_rows_detected': len(rows), 'back_row_parallel': back_ok,
        'cue_axis_deg': None if not cue else round(cue[0], 1),
        'label_CUE': any('CUE' in t.upper() for t in texts),
    }
    res['pass'] = {
        'playfield_2to1': bool(play) and near(play[2] / play[3], 2.0, 0.06),
        'corners': corners_hit == 4,
        'sides': sides_hit == 2,
        'diamonds_long': len(long_diamonds) >= 5 and not at_fifty,
        'rack_apex': apex_ok,
        'rack_rows': len(rows) >= 4,
        'back_row_parallel': back_ok,
        'balls_inside': len(balls) >= 14 and len(inside) == len(balls),
        'cue_angle': bool(cue) and 20 <= cue[0] <= 45,
        'label_CUE': res['label_CUE'],
    }
    # direction matters as much as angle: the butt must be on the head side (x < cue ball), away from the rack
    butt_x = None
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        ang = math.radians(eff.get(id(el), 0.0))
        if tag == 'line':
            x1, y1, x2, y2 = n(el, 'x1'), n(el, 'y1'), n(el, 'x2'), n(el, 'y2')
            if math.hypot(x2 - x1, y2 - y1) < 15:
                continue
            far = (x1, y1) if x1 > x2 else (x2, y2)   # the end with the larger x
            butt_x = far[0]
        elif tag == 'rect' and n(el, 'width') >= 15 and n(el, 'height') <= 2:
            butt_x = n(el, 'x') + n(el, 'width') * math.cos(ang)
    res['cue_butt_x'] = None if butt_x is None else round(butt_x, 2)
    res['pass']['cue_butt_side'] = butt_x is not None and butt_x < 25
    res['score'] = '%d/%d' % (sum(res['pass'].values()), len(res['pass']))
    return res


if __name__ == '__main__':
    for p in sys.argv[1:]:
        print(json.dumps(analyse(p), indent=1))
