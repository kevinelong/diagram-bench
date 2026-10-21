"""check_ace_geometry.py - assert the ACE constants directly, in ACE's own coordinate system.

The v3 prompt asks for viewBox="-8 -8 116 66" and the app's constants (POCKET_ZONES, BALL_RADIUS 1.125, diamonds at
eighths with 50 omitted, head spot (25,25), foot spot (75,25), rack rows spaced BALL_D*sin60 = 1.949). Those are
numbers, so the check is an assertion rather than an opinion.

Two facts keep the measurement honest:

* Every quantity here is read in **SVG user units**, never in pixels. Renders are letterboxed inside the viewport,
  so a pixel-space ratio would depend on the fit, not on the drawing; user units are invariant to that. The one way
  to lose the correspondence is a root `viewBox` that is not the app's, so that is guarded explicitly: a mismatch is
  reported (and the CLI exits 3) rather than silently mis-measuring a coordinate system we do not know.
* Transformations are composed down the tree (translate/rotate/scale/matrix, including rotate-about-a-point), so a
  cue inside `<g transform="rotate(30 25 25)">` is measured where it is drawn, not where its attributes sit.

Usage (cmd.exe):
    .venv\\Scripts\\python.exe check_ace_geometry.py <file.svg> [...]
    .venv\\Scripts\\python.exe check_ace_geometry.py --self-test
"""
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG = '{http://www.w3.org/2000/svg}'
VIEWBOX = (-8.0, -8.0, 116.0, 66.0)
CORNERS = [(1.5, 1.5), (98.5, 1.5), (1.5, 48.5), (98.5, 48.5)]
SIDES = [(50.0, 1.0), (50.0, 49.0)]
LONG_DIAMONDS = [12.5, 25.0, 37.5, 62.5, 75.0, 87.5]
BALL_R = 1.125
ROW_SPACING = BALL_R * 2 * math.sin(math.pi / 3)   # 1.949
HEAD_SPOT = (25.0, 25.0)
FOOT_SPOT = (75.0, 25.0)
PLAY_X, PLAY_Y, PLAY_W, PLAY_H = 0.0, 0.0, 100.0, 50.0

# check -> order is stable so the JSON and the score line are reproducible
CHECKS = ('playfield_2to1', 'corners', 'sides', 'diamonds_long', 'rack_apex', 'rack_rows',
          'back_row_parallel', 'balls_inside', 'cue_angle', 'label_CUE', 'cue_butt_side')

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# ----------------------------------------------------------------------------- small utilities

def n(el, a, d=0.0):
    try:
        return float(str(el.get(a, d)).replace('px', ''))
    except Exception:
        return d


def near(a, b, tol):
    return abs(a - b) <= tol


def dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


# ----------------------------------------------------------------------------- transforms

def mat_mul(m, k):
    """m applied after k."""
    return (m[0] * k[0] + m[2] * k[1],
            m[1] * k[0] + m[3] * k[1],
            m[0] * k[2] + m[2] * k[3],
            m[1] * k[2] + m[3] * k[3],
            m[0] * k[4] + m[2] * k[5] + m[4],
            m[1] * k[4] + m[3] * k[5] + m[5])


def mat_apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def mat_scale(m):
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


def mat_angle(m):
    return math.degrees(math.atan2(m[1], m[0]))


def parse_transform(s):
    m = IDENT
    for name, args in re.findall(r'([A-Za-z]+)\s*\(([^)]*)\)', s or ''):
        v = [float(x) for x in re.findall(r'-?\d*\.?\d+(?:[eE][-+]?\d+)?', args)]
        if name == 'translate' and v:
            k = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == 'scale' and v:
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            k = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == 'rotate' and v:
            a = math.radians(v[0])
            c, s = math.cos(a), math.sin(a)
            k = (c, s, -s, c, 0.0, 0.0)
            if len(v) >= 3:   # rotate about a point
                cx, cy = v[1], v[2]
                k = mat_mul((1.0, 0.0, 0.0, 1.0, cx, cy), mat_mul(k, (1.0, 0.0, 0.0, 1.0, -cx, -cy)))
        elif name == 'matrix' and len(v) == 6:
            k = tuple(v)
        else:
            continue
        m = mat_mul(m, k)
    return m


def walk(el, m=IDENT):
    """yield (element, accumulated matrix) honouring inherited group transforms."""
    here = mat_mul(m, parse_transform(el.get('transform')))
    yield el, here
    for child in el:
        yield from walk(child, here)


def read_viewbox(root):
    raw = root.get('viewBox') or ''
    vals = [float(x) for x in re.findall(r'-?\d*\.?\d+(?:[eE][-+]?\d+)?', raw)]
    return vals if len(vals) == 4 else None


# ----------------------------------------------------------------------------- element extraction

def circles_of(root, eff):
    """circles and ellipses as (x, y, r) in user units, transforms applied."""
    out = []
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        if tag not in ('circle', 'ellipse'):
            continue
        m = eff.get(id(el), IDENT)
        x, y = mat_apply(m, (n(el, 'cx'), n(el, 'cy')))
        s = mat_scale(m)
        if tag == 'circle':
            r = n(el, 'r') * s
        else:
            r = (n(el, 'rx') + n(el, 'ry')) / 2.0 * s
        out.append((round(x, 6), round(y, 6), round(r, 6)))
    return out


def walk_rects(root, eff):
    """(attribute x, y, w, h, effective rotation, transformed bbox) for every rect."""
    out = []
    for el in root.iter(SVG + 'rect'):
        x, y, w, h = n(el, 'x'), n(el, 'y'), n(el, 'width'), n(el, 'height')
        m = eff.get(id(el), IDENT)
        pts = [mat_apply(m, p) for p in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bbox = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        out.append((x, y, w, h, mat_angle(m), bbox))
    return out


# ----------------------------------------------------------------------------- the analysis

def analyse(path):
    p = Path(path)
    root = ET.fromstring(p.read_text(encoding='utf-8'))
    eff = {id(el): m for el, m in walk(root)}
    warnings = []

    vb = read_viewbox(root)
    vb_ok = bool(vb) and all(abs(a - b) <= 1e-6 for a, b in zip(vb, VIEWBOX))
    if not vb_ok:
        warnings.append('viewBox %r is not the app viewBox %r; user-unit geometry is not ACE-relative'
                        % (root.get('viewBox'), list(VIEWBOX)))

    circles = circles_of(root, eff)
    rects = walk_rects(root, eff)
    polys = [poly.get('points', '') for poly in root.iter(SVG + 'polygon')]
    texts = [' '.join(t.itertext()).strip() for t in root.iter(SVG + 'text')]

    # playfield: the widest rect whose transformed bbox is 2:1 (the cloth, not the rail frame)
    play = [r[5] for r in rects
            if r[5][2] > 30 and r[5][3] > 0 and near(r[5][2] / r[5][3], 2.0, 0.06)] if rects else []
    play = max(play, key=lambda b: (b[2] * b[3], -b[0], -b[1])) if play else None

    # pockets: circles/ellipses of pocket size whose centre sits within tolerance of a zone.
    # Tolerance lets a pocket drawn exactly on the cloth boundary ((0,0), (50,0), (100,50)) register as the
    # app's inset zone; balls (r = 1.125) and spots (r = 0.2..0.25) are excluded by radius.
    pockets = [c for c in circles if 1.6 <= c[2] <= 5.0]

    def pocket_at(target, tol):
        return [c for c in pockets if near(c[0], target[0], tol) and near(c[1], target[1], tol)]

    corners_hit = sum(1 for t in CORNERS if pocket_at(t, 2.6))
    sides_hit = sum(1 for t in SIDES if pocket_at(t, 2.2))

    # balls: radius within 40% of BALL_R
    balls = [c for c in circles if near(c[2], BALL_R, BALL_R * 0.45)]
    inside = [b for b in balls if PLAY_X + b[2] <= b[0] <= PLAY_X + PLAY_W - b[2]
              and PLAY_Y + b[2] <= b[1] <= PLAY_Y + PLAY_H - b[2]]

    # diamond markers: polygon centroids plus rotated small rects (rotated = transform angle at least 30 off 0/90)
    def centroid(points):
        nums = [float(v) for v in re.findall(r'-?[\d.]+', points)]
        xs, ys = nums[0::2], nums[1::2]
        return (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else (None, None)

    cents = [centroid(q) for q in polys]
    for x, y, w, h, ang, _bbox in rects:
        if 0.2 <= w <= 1.2 and 0.2 <= h <= 1.2 and abs(ang % 90) >= 30:
            cents.append((x + w / 2, y + h / 2))
    long_diamonds = set()
    for x, y in cents:
        if x is None:
            continue
        for d in LONG_DIAMONDS:
            if near(x, d, 1.0) and (near(y, -3, 2.0) or near(y, 53, 2.0)):
                long_diamonds.add(d)
    at_fifty = any(x is not None and near(x, 50, 1.0) and (near(y, -3, 2.0) or near(y, 53, 2.0))
                   for x, y in cents)

    # rack: balls inside the cloth, away from the head spot, apex nearest the foot spot
    rack = [b for b in inside if b[0] > 40]
    apex = min(rack, key=lambda b: (dist((b[0], b[1]), FOOT_SPOT), b[0], b[1])) if rack else None
    apex_ok = bool(apex) and near(apex[0], FOOT_SPOT[0], 1.5) and near(apex[1], FOOT_SPOT[1], 1.5)
    rows = sorted({round(b[0] / ROW_SPACING) for b in rack}) if rack else []
    back = sorted(rack, key=lambda b: (-b[0], b[1]))[:5]
    back_ok = len(back) == 5 and (max(b[0] for b in back) - min(b[0] for b in back)) <= 2.2 * BALL_R

    # cue ball: the ball nearest the head spot (fall back to the spot itself)
    cue_ball = HEAD_SPOT
    if balls:
        cand = min(balls, key=lambda b: (dist((b[0], b[1]), HEAD_SPOT), b[0], b[1]))
        if dist((cand[0], cand[1]), HEAD_SPOT) <= 12.0:
            cue_ball = (cand[0], cand[1])
        else:
            warnings.append('no ball near the head spot; cue geometry referenced to %r' % (HEAD_SPOT,))

    # cue candidates: any long thin element, then *prefer the one at the cue ball* so a rail edge or a rack
    # edge drawn as a line cannot be taken for the stick.
    cands = []
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        m = eff.get(id(el), IDENT)
        if tag == 'line':
            a = mat_apply(m, (n(el, 'x1'), n(el, 'y1')))
            b = mat_apply(m, (n(el, 'x2'), n(el, 'y2')))
        elif tag == 'rect' and n(el, 'width') >= 15 and n(el, 'height') <= 2:
            w, h = n(el, 'width'), n(el, 'height')
            a = mat_apply(m, (n(el, 'x'), n(el, 'y') + h / 2))
            b = mat_apply(m, (n(el, 'x') + w, n(el, 'y') + h / 2))
        else:
            continue
        length = dist(a, b)
        if length >= 15:
            cands.append((a, b, length))

    cue = None
    cue_near_ball = False
    if cands:
        scored = [(seg_dist(cue_ball, a, b), -length, a, b, length) for a, b, length in cands]
        touching = [s for s in scored if s[0] <= 6.0]
        if touching:
            pick = min(touching, key=lambda s: (s[0], s[1]))
            cue_near_ball = True
        else:
            pick = min(scored, key=lambda s: s[1])   # longest, as a fallback only
            warnings.append('no long thin element within 6 units of the cue ball; used the longest instead')
        _d, _neg, a, b, length = pick
        raw = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        axis = abs(((raw + 90) % 180) - 90)
        # the tip touches the cue ball; the butt is the end away from it, whichever direction that is
        da, db = dist(cue_ball, a), dist(cue_ball, b)
        butt, tip = (a, b) if da >= db else (b, a)
        cue = (axis, length, butt, tip)

    res = {
        'file': p.name,
        'viewBox': vb, 'viewBox_ok': vb_ok,
        'playfield': None if not play else [round(v, 1) for v in play],
        'corners_hit': corners_hit, 'sides_hit': sides_hit,
        'balls': len(balls), 'balls_inside': len(inside),
        'long_diamonds_found': sorted(long_diamonds), 'diamond_at_50': at_fifty,
        'apex_on_foot_spot': apex_ok, 'rack_rows_detected': len(rows), 'back_row_parallel': back_ok,
        'cue_axis_deg': None if not cue else round(cue[0], 1),
        'cue_length': None if not cue else round(cue[1], 2),
        'cue_near_cue_ball': cue_near_ball,
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
        'cue_butt_side': bool(cue) and cue[2][0] < cue_ball[0],
    }
    res['cue_butt_x'] = None if not cue else round(cue[2][0], 2)
    res['cue_tip_x'] = None if not cue else round(cue[3][0], 2)
    res['score'] = '%d/%d' % (sum(res['pass'].values()), len(CHECKS))
    res['valid'] = vb_ok
    res['warnings'] = warnings
    return res


# ----------------------------------------------------------------------------- fixtures (generated, not hand-written)

def _f(v):
    return ('%.4f' % v).rstrip('0').rstrip('.')


def _circle(x, y, r, fill='white', stroke='black'):
    return ('  <circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="0.1"/>'
            % (_f(x), _f(y), _f(r), fill, stroke))


def _ellipse(x, y, r):
    return ('  <ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="black"/>'
            % (_f(x), _f(y), _f(r), _f(r)))


def _cloth():
    return '  <rect x="0" y="0" width="100" height="50" fill="lightseagreen"/>\n'


def _pockets(sides=SIDES, side_tag='ellipse'):
    out = [_circle(cx, cy, 3.5, 'black', 'black') for cx, cy in CORNERS]
    if side_tag == 'ellipse':
        out += [_ellipse(cx, cy, 2.5) for cx, cy in sides]
    else:
        out += [_circle(cx, cy, 2.5, 'black', 'black') for cx, cy in sides]
    return '\n'.join(out) + '\n'


def _diamond(cx, cy):
    return ('  <rect x="%s" y="%s" width="0.5" height="0.5" fill="none" stroke="gold" '
            'transform="rotate(45 %s %s)"/>'
            % (_f(cx - 0.25), _f(cy - 0.25), _f(cx), _f(cy)))


def _diamonds():
    out = []
    for d in LONG_DIAMONDS:
        out.append(_diamond(d, -3))
        out.append(_diamond(d, 53))
    for y in (12.5, 25.0, 37.5):
        out.append(_diamond(-3, y))
        out.append(_diamond(103, y))
    return '\n'.join(out) + '\n'


def _spots():
    return _circle(*HEAD_SPOT, 0.2, 'black', 'black') + '\n' + _circle(*FOOT_SPOT, 0.2, 'black', 'black') + '\n'


def _rack(rhombus=False):
    """ACE lattice: x = 75 + k*1.9493, y = 25 + (j - k/2)*2.25. Rhombus is v3's real defect: x spreads
    2.25 inside a row while rows advance only 1.125 in x (and 1.9493 in y), so the back five span 3.4 units
    of x instead of 0."""
    out = []
    for k in range(5):
        for j in range(k + 1):
            if rhombus:
                x = 75.0 + (j - k / 2.0) * 2.25
                y = 25.0 + k * ROW_SPACING
            else:
                x = 75.0 + k * ROW_SPACING
                y = 25.0 + (j - k / 2.0) * 2.25
            out.append(_circle(x, y, BALL_R))
    return '\n'.join(out) + '\n'


def _cue_line(p_butt, p_tip):
    return ('  <line x1="%s" y1="%s" x2="%s" y2="%s" stroke="black" stroke-width="0.9"/>\n'
            % (_f(p_butt[0]), _f(p_butt[1]), _f(p_tip[0]), _f(p_tip[1])))


def _label():
    return ('  <text x="25" y="21.5" font-family="Arial" font-size="2.5" text-anchor="middle" '
            'fill="white">CUE</text>\n')


def _svg(body, viewbox='-8 -8 116 66'):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s">\n%s</svg>\n' % (viewbox, body))


def _body(sides=SIDES, side_tag='ellipse', rhombus=False, cue=None, extra_balls=()):
    cue = cue or ((25 - 25 * math.cos(math.radians(30)), 25 + 25 * math.sin(math.radians(30))), HEAD_SPOT)
    back = ''.join(_circle(x, y, BALL_R) + '\n' for x, y in extra_balls)
    return (_cloth() + _pockets(sides, side_tag) + _diamonds() + _spots() + _rack(rhombus)
            + _circle(*HEAD_SPOT, BALL_R) + '\n' + back + _cue_line(*cue) + _label())


FIXTURE_TEXT = {
    # exactly the ACE constants: 11/11
    'good.svg': _svg(_body()),
    # side pockets on the short rails instead of (50,1)/(50,49)
    'bad_pockets_short_rail.svg': _svg(_body(sides=[(1.5, 12.5), (1.5, 37.5)])),
    # the v3 defect: rows advance x by 1.125 and spread 2.25 inside a row, so the back five span 3.4 units
    'bad_rack_rhombus.svg': _svg(_body(rhombus=True)),
    # a horizontal stick: angle check only (the butt is still on the head side)
    'bad_cue_horizontal.svg': _svg(_body(cue=((1.0, 25.0), HEAD_SPOT))),
    # the v4 defect: (25,25) -> (48.736,12.5), butt pointing at the rack
    'bad_cue_butt_inverted.svg': _svg(_body(cue=((48.736, 12.5), HEAD_SPOT))),
    # three balls beyond the foot rail
    'bad_balls_outside.svg': _svg(_body(extra_balls=[(103.0, 25.0), (104.0, 20.0), (104.5, 30.0)])),
    # pockets drawn exactly on the cloth boundary: same verdict as good, via the tolerance path
    'good_boundary_pockets.svg': _svg(_body(sides=[(50.0, 0.0), (50.0, 50.0)], side_tag='circle')),
    # wrong viewBox: geometry is unchanged in user units, but the coordinate system is not the app's
    'bad_viewbox.svg': _svg(_body(), viewbox='-8 -8 58 33'),
}

# file -> (checks that must fail) ; valid is asserted separately
EXPECT = {
    'good.svg': frozenset(),
    'bad_pockets_short_rail.svg': frozenset({'sides'}),
    'bad_rack_rhombus.svg': frozenset({'back_row_parallel'}),
    'bad_cue_horizontal.svg': frozenset({'cue_angle'}),
    'bad_cue_butt_inverted.svg': frozenset({'cue_butt_side'}),
    'bad_balls_outside.svg': frozenset({'balls_inside'}),
    'good_boundary_pockets.svg': frozenset(),
    'bad_viewbox.svg': frozenset(),
}
EXPECT_VALID = {'bad_viewbox.svg': False}


def build_fixtures():
    FIXTURES.mkdir(exist_ok=True)
    for name in sorted(FIXTURE_TEXT):
        (FIXTURES / name).write_text(FIXTURE_TEXT[name], encoding='utf-8')
    return sorted(FIXTURE_TEXT)


def self_test():
    ok_all = True
    print('%-4s %-27s %-6s %-7s %s' % ('', 'fixture', 'score', 'viewBox', 'failing checks'))
    for name in build_fixtures():
        res = analyse(str(FIXTURES / name))
        fails = frozenset(k for k, v in res['pass'].items() if not v)
        want_valid = EXPECT_VALID.get(name, True)
        ok = fails == EXPECT[name] and res['valid'] == want_valid
        if name == 'good.svg':
            ok = ok and res['score'] == '11/11'
        ok_all = ok_all and ok
        print('%-4s %-27s %-6s %-7s %s' % ('PASS' if ok else 'FAIL', name, res['score'],
                                           'ok' if res['valid'] else 'MISMATCH',
                                           ','.join(sorted(fails)) or '-'))
        if not ok:
            exp = ','.join(sorted(EXPECT[name])) or '-'
            print('     expected failing: %s ; viewBox_ok: %s ; warnings: %s'
                  % (exp, want_valid, res['warnings']))
    print('SELF-TEST PASS' if ok_all else 'SELF-TEST FAIL')
    return ok_all


def main(argv):
    args = [a for a in argv[1:]]
    if not args:
        print('usage: check_ace_geometry.py <file.svg> [...] | --self-test', file=sys.stderr)
        return 2
    if '--self-test' in args:
        return 0 if self_test() else 1
    rc = 0
    for name in args:
        res = analyse(name)
        print(json.dumps(res, indent=1, sort_keys=True))
        if not res['valid']:
            print('VIEWBOX GUARD: %s has viewBox %r, expected %r - refusing to call user-unit geometry valid'
                  % (name, res['viewBox'], list(VIEWBOX)), file=sys.stderr)
            rc = 3
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv))
