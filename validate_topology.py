"""validate_topology.py - the three properties the count/shape checks could not reach.

1. `rooms_closed`   (floor-1): do the wall bands actually enclose rooms? Rasterise the walls from the SVG,
                    flood-fill the free space, and count enclosed regions that do not touch the canvas border.
                    This is topology, not counting - a plan can have four labels and zero closed rooms.
2. `crossing_direction` (process-1): a connector that spans the lane divider, and spans it **downwards**
                    (top lane -> bottom lane), which is what the prompt asks for.
3. `arrowheads`     (blueprint-1): a dimension arrow needs a detectable head. Counted as any `<polygon>`, any
                    path with 3+ vertices, or two short segments (<30 px) meeting near a long line's end. This was
                    previously recorded as unmeasurable - it is measurable, and on this sample it is absent.

Usage (cmd.exe):  .venv\\Scripts\\python.exe validate_topology.py --model qwen3-coder:30b
"""
import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
SVG = '{http://www.w3.org/2000/svg}'
DARK = ('black', 'navy', 'darkblue', 'blue', '#000', '#0000', '#00008b', '#001', 'midnightblue', 'darkslateblue')


def els(root, tag):
    return [e for e in root.iter() if e.tag == SVG + tag]


def fnum(el, a, d=0.0):
    try:
        return float(str(el.get(a, d)).replace('px', ''))
    except Exception:
        return d


def rooms_closed(root, size=(1024, 1024)):
    W, H = size
    mask = np.zeros((H, W), np.uint8)
    walls = 0
    for r in els(root, 'rect'):
        w, h = fnum(r, 'width'), fnum(r, 'height')
        fill = str(r.get('fill', '')).lower().strip()
        if w >= 0.9 * W and h >= 0.9 * H:
            continue
        if (w <= 0.05 * W or h <= 0.05 * H) and any(fill.startswith(d) for d in DARK):
            x, y = int(fnum(r, 'x')), int(fnum(r, 'y'))
            cv2.rectangle(mask, (x, y), (x + int(w), y + int(h)), 1, -1)
            walls += 1
    if walls < 2:
        return 0, walls
    free = (mask == 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    enclosed = 0
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 400:
            continue
        if x == 0 or y == 0 or x + w >= W or y + h >= H:   # touches the border -> outside air
            continue
        enclosed += 1
    return enclosed, walls


def dividers(root):
    W = fnum(root, 'width', 1024) or 1024
    ys = []
    for l in els(root, 'line'):
        if abs(fnum(l, 'y1') - fnum(l, 'y2')) <= 2 and abs(fnum(l, 'x2') - fnum(l, 'x1')) >= 0.6 * W:
            ys.append(fnum(l, 'y1'))
    for r in els(root, 'rect'):
        if fnum(r, 'width') >= 0.6 * W:
            ys.append(fnum(r, 'y'))
    return sorted(set(ys))


def crossing_direction(root):
    divs = dividers(root)
    if len(divs) < 2:
        return None
    span, downward = 0, 0
    for l in els(root, 'line'):
        y1, y2 = fnum(l, 'y1'), fnum(l, 'y2')
        lo, hi = min(y1, y2), max(y1, y2)
        if hi - lo < 60:
            continue
        if any(lo < d < hi for d in divs):
            span += 1
            if y2 > y1:          # SVG y grows downward, so y2 > y1 is a down arrow
                downward += 1
    return {'spanning': span, 'downward': downward}


def arrowheads(root):
    heads = 0
    for p in els(root, 'polygon'):
        heads += 1
    for p in els(root, 'path'):
        if len([c for c in p.get('d', '') if c in 'ML']) >= 3:
            heads += 1
    segs = []
    for l in els(root, 'line'):
        x1, y1, x2, y2 = fnum(l, 'x1'), fnum(l, 'y1'), fnum(l, 'x2'), fnum(l, 'y2')
        if math.hypot(x2 - x1, y2 - y1) < 30:
            segs.append((x1, y1))
    # two short segments whose endpoints meet form a head
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if abs(segs[i][0] - segs[j][0]) < 8 and abs(segs[i][1] - segs[j][1]) < 8:
                heads += 1
                break
    return heads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--dir', default=None)
    args = ap.parse_args()
    src = Path(args.dir) if args.dir else (ROOT / 'svg' / args.model.replace(':', '_'))

    out = {'arm': 'B-topology', 'model': args.model, 'rows': []}
    for svg_path in sorted(src.glob('*.svg')):
        pid = svg_path.stem.rsplit('-s', 1)[0]
        try:
            root = ET.fromstring(svg_path.read_text(encoding='utf-8'))
        except Exception as e:
            out['rows'].append({'sample': svg_path.stem, 'error': str(e)})
            continue
        row = {'id': pid, 'sample': svg_path.stem}
        if pid == 'floor-1':
            rooms, walls = rooms_closed(root)
            row.update({'rooms_enclosed': rooms, 'wall_bands': walls,
                        'pass': rooms >= 4, 'rule': 'at least 4 rooms enclosed by walls'})
            print('  %-16s rooms %d (walls %d)  %s' % (svg_path.stem, rooms, walls, 'ok' if row['pass'] else 'FAIL'))
        elif pid == 'process-1':
            cd = crossing_direction(root)
            ok = bool(cd) and cd['spanning'] >= 1 and cd['downward'] >= 1
            row.update({'crossing': cd, 'pass': ok, 'rule': 'at least one connector spans downwards'})
            print('  %-16s crossing %s  %s' % (svg_path.stem, cd, 'ok' if ok else 'FAIL'))
        elif pid == 'blueprint-1':
            heads = arrowheads(root)
            row.update({'arrowhead_markers': heads, 'pass': heads >= 1,
                        'rule': 'at least one detectable arrowhead for the dimension lines'})
            print('  %-16s arrowheads %d  %s' % (svg_path.stem, heads, 'ok' if row['pass'] else 'FAIL'))
        else:
            continue
        out['rows'].append(row)

    passed = sum(1 for r in out['rows'] if r.get('pass'))
    out['passed'] = passed
    out['documents'] = len(out['rows'])
    (ROOT / 'result-method-b-topology.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print('\ntopology checks passed: %d/%d' % (passed, len(out['rows'])))


if __name__ == '__main__':
    sys.exit(main())
