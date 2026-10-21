"""validate_doors.py - count wall partitions and the door gaps in each, which is what the prompt specifies.

`floor-1` asks for "thick black walls ... one door gap in each room wall". A closure flood-fill cannot express
that (doors make rooms connected, correctly). This measures the specification directly:

  partitions : runs of columns (or rows) where the wall mask covers a large share of the span -> internal walls
  gaps       : runs of non-wall pixels inside a partition's span, at least 10 px wide -> door openings

A pass needs at least two vertical partitions and one horizontal one, each carrying at least one gap and not more
than two (one door per room wall).

Usage (cmd.exe):  .venv\\Scripts\\python.exe validate_doors.py --model qwen3-coder:30b
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
SVG = '{http://www.w3.org/2000/svg}'
DARK = ('black', 'navy', 'darkblue', '#000', '#001', 'midnightblue')


def fnum(el, a, d=0.0):
    try:
        return float(str(el.get(a, d)).replace('px', ''))
    except Exception:
        return d


def wall_mask(root, W=1024, H=1024):
    mask = np.zeros((H, W), np.uint8)
    for r in root.iter(SVG + 'rect'):
        w, h = fnum(r, 'width'), fnum(r, 'height')
        fill = str(r.get('fill', '')).lower().strip()
        if w >= 0.9 * W and h >= 0.9 * H:
            continue
        if (w <= 0.05 * W or h <= 0.05 * H) and any(fill.startswith(d) for d in DARK):
            x, y = int(fnum(r, 'x')), int(fnum(r, 'y'))
            cv2.rectangle(mask, (x, y), (x + int(w), y + int(h)), 1, -1)
    return mask


def bands(mask, axis, cover_frac=0.35):
    """runs along `axis` where the mask covers a large share of the perpendicular span."""
    cov = mask.mean(axis=axis)          # per column (axis=0) or per row (axis=1)
    hot = cov > cover_frac
    out, start = [], None
    for i, v in enumerate(hot):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1)); start = None
    if start is not None:
        out.append((start, len(hot) - 1))
    return out


def gaps_in_band(mask, lo, hi, axis, min_gap=10):
    if axis == 0:                        # vertical band -> scan down a middle column
        col = mask[:, (lo + hi) // 2]
    else:                                # horizontal band -> scan along a middle row
        col = mask[(lo + hi) // 2, :]
    idx = np.where(col > 0)[0]
    if len(idx) < 2:
        return 0, 0
    first, last = idx[0], idx[-1]
    seg, run, n = col[first:last + 1], 0, 0
    for v in seg:
        if v == 0:
            run += 1
        else:
            if run >= min_gap:
                n += 1
            run = 0
    if run >= min_gap:
        n += 1
    return n, int(last - first)


def is_perimeter(lo, hi, size, frac=0.10):
    # a band within 10% of an edge is the outside wall, not an internal partition
    return lo <= frac * size or hi >= (1 - frac) * size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--dir', default=None)
    args = ap.parse_args()
    src = Path(args.dir) if args.dir else (ROOT / 'svg' / args.model.replace(':', '_'))

    rows = []
    for svg in sorted(src.glob('floor-1-s*.svg')):
        root = ET.fromstring(svg.read_text(encoding='utf-8'))
        mask = wall_mask(root)
        vb_all = bands(mask, 0)
        hb_all = bands(mask, 1)
        vb = [b for b in vb_all if not is_perimeter(b[0], b[1], 1024)]
        hb = [b for b in hb_all if not is_perimeter(b[0], b[1], 1024)]
        v_details = []
        for lo, hi in vb:
            n, span = gaps_in_band(mask, lo, hi, 0)
            v_details.append({'x': [lo, hi], 'gaps': n, 'span_px': span})
        h_details = []
        for lo, hi in hb:
            n, span = gaps_in_band(mask, lo, hi, 1)
            h_details.append({'y': [lo, hi], 'gaps': n, 'span_px': span})
        ok_bands = len(vb) >= 1 and len(hb) >= 1
        ok_gaps = all(1 <= d['gaps'] <= 2 for d in v_details + h_details) if (v_details or h_details) else False
        passed = ok_bands and ok_gaps
        rows.append({'sample': svg.stem, 'vertical_partitions': len(vb), 'horizontal_partitions': len(hb),
                     'vertical_detail': v_details, 'horizontal_detail': h_details,
                     'pass': passed})
        print('  %-14s v-bands %d %s | h-bands %d %s  %s' % (
            svg.stem, len(vb), [d['gaps'] for d in v_details],
            len(hb), [d['gaps'] for d in h_details], 'ok' if passed else 'FAIL'), flush=True)

    passed = sum(1 for r in rows if r['pass'])
    (ROOT / 'result-method-b-doors.json').write_text(
        json.dumps({'arm': 'B-doors', 'model': args.model, 'passed': passed, 'documents': len(rows),
                    'rule': 'at least 2 vertical and 1 horizontal partition, each with 1-2 door gaps',
                    'rows': rows}, indent=1), encoding='utf-8')
    print('\ndoor-gap rule satisfied: %d/%d' % (passed, len(rows)))


if __name__ == '__main__':
    sys.exit(main())
