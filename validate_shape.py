"""validate_shape.py - deterministic shape checks for the five prompts where counting is blind.

Every measure is computed from the SVG source against `shape-rules.json` (frozen before the first run):
lanes, lane crossings, circles, marker-terminated lines, background darkness, thick strokes, nodal layout.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe validate_shape.py --model qwen3-coder:30b
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SVG = '{http://www.w3.org/2000/svg}'
DARK = ('black', 'navy', 'darkblue', 'blue', '#000', '#0000', '#00008b', '#001', 'midnightblue', 'darkslateblue')


def parse(path):
    return ET.fromstring(Path(path).read_text(encoding='utf-8'))


def num(el, attr, default=0.0):
    try:
        return float(str(el.get(attr, default)).replace('px', ''))
    except Exception:
        return default


def elements(root):
    out = []
    for el in root.iter():
        tag = el.tag.replace(SVG, '')
        if tag in ('rect', 'line', 'circle', 'ellipse', 'path', 'text'):
            out.append((tag, el))
    return out


def measure(root, name):
    els = elements(root)
    W = num(root, 'width', 1024) or 1024
    H = num(root, 'height', 1024) or 1024

    def long_horizontal():
        n = 0
        for tag, el in els:
            if tag == 'line':
                x1, x2 = num(el, 'x1'), num(el, 'x2')
                y1, y2 = num(el, 'y1'), num(el, 'y2')
                if abs(y1 - y2) <= 2 and abs(x2 - x1) >= 0.6 * W:
                    n += 1
            elif tag == 'rect':
                if num(el, 'width') >= 0.6 * W and num(el, 'height') <= 40:
                    n += 1
        return n

    def text_all(values):
        texts = ' | '.join(' '.join(el.itertext()) for tag, el in els if tag == 'text').upper()
        return all(v.upper() in texts for v in values)

    def cross_lane():
        ys = []
        for tag, el in els:
            if tag == 'line':
                y1, y2 = num(el, 'y1'), num(el, 'y2')
                if abs(y1 - y2) >= 60:
                    ys.append((min(y1, y2), max(y1, y2)))
        lanes = long_horizontal()
        if lanes < 2:
            return 0
        mid = H / 2
        return sum(1 for a, b in ys if a < mid < b)

    def circles():
        return sum(1 for tag, _ in els if tag in ('circle', 'ellipse'))

    def marked_lines():
        return sum(1 for tag, el in els
                   if tag in ('line', 'path') and ('marker-end' in el.attrib or 'marker-start' in el.attrib))

    def background_dark():
        for tag, el in els:
            if tag == 'rect' and num(el, 'width') >= 0.9 * W and num(el, 'height') >= 0.9 * H:
                fill = str(el.get('fill', '')).lower().strip()
                return any(fill.startswith(d) for d in DARK)
        return False

    def lines():
        return sum(1 for tag, _ in els if tag == 'line')

    def parallel_close():
        seg = []
        for tag, el in els:
            if tag == 'line':
                x1, x2 = num(el, 'x1'), num(el, 'x2')
                y1, y2 = num(el, 'y1'), num(el, 'y2')
                if abs(x1 - x2) <= 2 and 8 <= abs(y2 - y1) <= 80:
                    seg.append(x1)
        seg.sort()
        return sum(1 for a, b in zip(seg, seg[1:]) if 0 < abs(b - a) <= max_gap)

    def thick_strokes(min_stroke):
        n = 0
        for tag, el in els:
            if tag in ('rect', 'line', 'path'):
                try:
                    if float(str(el.get('stroke-width', '1')).replace('px', '')) >= min_stroke:
                        n += 1
                except Exception:
                    pass
        return n

    def one_node_above():
        cy = sorted(num(el, 'cy', 1e9) for tag, el in els if tag in ('circle', 'ellipse'))
        if len(cy) < 3:
            return False
        return len({c for c in cy if c < cy[1] - 5}) == 1

    def connectors():
        n = 0
        for tag, el in els:
            if tag == 'line':
                n += 1
            elif tag == 'path':
                d = el.get('d', '')
                if d.count('C') + d.count('Q') <= 2:
                    n += 1
        return n

    table = {
        'long_horizontal_lines': long_horizontal,
        'circles': circles,
        'marked_lines': marked_lines,
        'background_is_dark': background_dark,
        'lines': lines,
        'parallel_close_lines': parallel_close,
        'connectors': connectors,
    }
    if name.startswith('thick_strokes'):
        return None  # handled by caller with its own parameter
    if name.startswith('text_contains_all'):
        return None
    return table[name]()


def run_rule(root, rule):
    m = rule['measure']
    if m == 'text_contains_all':
        return check_bool(_text_all(root, rule['values']))
    if m == 'thick_strokes':
        return check_number(_thick(root, rule.get('min_stroke', 4)), rule)
    if m == 'filled_narrow_rects':
        return check_number(_filled_walls(root), rule)
    if m == 'one_node_above_rest':
        return check_bool(_one_above(root))
    if m == 'background_is_dark':
        return check_bool(_dark(root))
    if m == 'cross_lane_connectors':
        return check_number(_cross(root), rule)
    if m == 'parallel_close_lines':
        max_gap = float(rule.get('max_gap_frac', 0.04)) * float(root.get('width', 1024) or 1024)
        return check_number(_parallel(root, max_gap), rule)
    return check_number(measure(root, m), rule)


def check_bool(ok):
    return ok, ('' if ok else 'failed')


def check_number(got, rule):
    lo = rule.get('expect_at_least')
    hi = rule.get('expect_at_most')
    if lo is not None and got < lo:
        return False, 'expected at least %s, got %s' % (lo, got)
    if hi is not None and got > hi:
        return False, 'expected at most %s, got %s' % (hi, got)
    return True, ''


def _text_all(root, values):
    texts = ' | '.join(' '.join(el.itertext()) for tag, el in elements(root) if tag == 'text').upper()
    return all(v.upper() in texts for v in values)


def _thick(root, min_stroke):
    n = 0
    for tag, el in elements(root):
        if tag in ('rect', 'line', 'path'):
            try:
                if float(str(el.get('stroke-width', '1')).replace('px', '')) >= min_stroke:
                    n += 1
            except Exception:
                pass
    return n


def _filled_walls(root):
    W = float(root.get('width', 1024) or 1024); H = float(root.get('height', 1024) or 1024)
    n = 0
    for tag, el in elements(root):
        if tag != 'rect':
            continue
        w, h = float(el.get('width', 0)), float(el.get('height', 0))
        fill = str(el.get('fill', '')).lower().strip()
        if w >= 0.9 * W and h >= 0.9 * H:
            continue
        if (w <= 0.05 * W or h <= 0.05 * H) and any(fill.startswith(d) for d in DARK):
            n += 1
    return n


def _one_above(root):
    cy = sorted(float(el.get('cy', 1e9)) for tag, el in elements(root) if tag in ('circle', 'ellipse'))
    return len(cy) >= 3 and len([c for c in cy if c < cy[1] - 5]) == 1


def _dark(root):
    W = float(root.get('width', 1024) or 1024); H = float(root.get('height', 1024) or 1024)
    for tag, el in elements(root):
        if tag == 'rect' and float(el.get('width', 0)) >= 0.9 * W and float(el.get('height', 0)) >= 0.9 * H:
            fill = str(el.get('fill', '')).lower().strip()
            return any(fill.startswith(d) for d in DARK)
    return False


def _divider(root):
    """y of the lane divider: the long horizontal line closest to the canvas middle."""
    H = float(root.get('height', 1024) or 1024)
    ys = []
    for tag, el in elements(root):
        if tag == 'line' and abs(float(el.get('y1', 0)) - float(el.get('y2', 0))) <= 2:
            ys.append(float(el.get('y1', 0)))
        elif tag == 'rect' and float(el.get('width', 0)) >= 0.6 * (float(root.get('width', 1024) or 1024)):
            ys.append(float(el.get('y', 0)))
    return min(ys, key=lambda y: abs(y - H / 2)) if ys else H / 2


def _dividers(root):
    """every long horizontal that could be a lane boundary."""
    W = float(root.get('width', 1024) or 1024)
    ys = []
    for tag, el in elements(root):
        if tag == 'line' and abs(float(el.get('y1', 0)) - float(el.get('y2', 0))) <= 2 and \
                abs(float(el.get('x2', 0)) - float(el.get('x1', 0))) >= 0.6 * W:
            ys.append(float(el.get('y1', 0)))
        elif tag == 'rect' and float(el.get('width', 0)) >= 0.6 * W:
            ys.append(float(el.get('y', 0)))
    return sorted(set(ys))


def _cross(root):
    """a connector counts once if it spans ANY detected lane boundary."""
    if _long(root) < 2:
        return 0
    divs = _dividers(root)
    n = 0
    for tag, el in elements(root):
        if tag == 'line':
            y1, y2 = float(el.get('y1', 0)), float(el.get('y2', 0))
            lo, hi = min(y1, y2), max(y1, y2)
            if hi - lo >= 60 and any(lo < d < hi for d in divs):
                n += 1
    return n


def _long(root):
    W = float(root.get('width', 1024) or 1024)
    n = 0
    for tag, el in elements(root):
        if tag == 'line':
            if abs(float(el.get('y1', 0)) - float(el.get('y2', 0))) <= 2 and \
                    abs(float(el.get('x2', 0)) - float(el.get('x1', 0))) >= 0.6 * W:
                n += 1
        elif tag == 'rect' and float(el.get('width', 0)) >= 0.6 * W and float(el.get('height', 0)) <= 40:
            n += 1
    return n


def _parallel(root, max_gap=40.0):
    seg = []
    for tag, el in elements(root):
        if tag == 'line':
            x1, x2 = float(el.get('x1', 0)), float(el.get('x2', 0))
            y1, y2 = float(el.get('y1', 0)), float(el.get('y2', 0))
            if abs(x1 - x2) <= 2 and 8 <= abs(y2 - y1) <= 400:
                seg.append(x1)
    seg.sort()
    return sum(1 for a, b in zip(seg, seg[1:]) if 0 < abs(b - a) <= max_gap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--dir', default=None)
    args = ap.parse_args()

    rules = json.loads((ROOT / 'shape-rules.json').read_text(encoding='utf-8'))['rules']
    src = Path(args.dir) if args.dir else (ROOT / 'svg' / args.model.replace(':', '_'))
    rows = []
    for pid, spec in rules.items():
        for svg_path in sorted(src.glob(pid + '-s*.svg')):
            try:
                root = parse(svg_path)
            except Exception as e:
                rows.append({'id': pid, 'sample': svg_path.stem, 'error': str(e)})
                print('  %-16s PARSE ERROR' % svg_path.stem)
                continue
            results = []
            for rule in spec['checks']:
                ok, note = run_rule(root, rule)
                results.append({'check': rule['id'], 'ok': ok, 'note': note, 'q': rule['q']})
            passed = sum(1 for r in results if r['ok'])
            rows.append({'id': pid, 'sample': svg_path.stem, 'passed': passed, 'total': len(results),
                         'results': results})
            print('  %-16s %d/%d %s' % (svg_path.stem, passed, len(results),
                                        '' if passed == len(results) else
                                        ' | '.join('%s: %s' % (r['check'], r['note']) for r in results if not r['ok'])),
                  flush=True)
    ok_all = sum(1 for r in rows if r.get('passed') == r.get('total'))
    payload = {'arm': 'B-shape', 'model': args.model, 'documents': len(rows),
               'documents_passing_all_shape_checks': ok_all, 'rows': rows}
    (ROOT / 'result-method-b-shape.json').write_text(json.dumps(payload, indent=1), encoding='utf-8')
    print('\nshape checks fully passed: %d/%d documents' % (ok_all, len(rows)))


if __name__ == '__main__':
    sys.exit(main())
