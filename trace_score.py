"""trace_score.py - score one raster for the vector-traceability job.

Metrics, all computed locally and deterministically:
  paths, nodes      : vtracer SVG path count and total path-command count (economy)
  axis_pct          : share of detected straight segments within 2 degrees of H/V
  straight_px       : share of edge pixels explained by long straight segments
  flat_pct          : share of pixels whose local gradient is below a sharp-edge threshold
  colours           : unique colours after 5-bit quantisation (flat fills -> small)
  ink_pct           : share of non-background pixels (a blank image is not a good sign)

Usage: trace_score.py image.png [--json out.json]
Run through cmd.exe: .venv\Scripts\python.exe trace_score.py image.png
"""
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import vtracer


def trace_stats(svg_path):
    text = Path(svg_path).read_text(encoding='utf-8', errors='replace')
    paths = text.count('<path')
    nodes = text.count('C') + text.count('L') + text.count('M') + text.count('Q')
    return paths, nodes


def score(png_path):
    img = cv2.imread(str(png_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return {'error': 'unreadable'}
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3:4] / 255.0
        img = (img[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40, minLineLength=40, maxLineGap=3)
    axis = 0
    total = 0
    segs = [] if lines is None else np.asarray(lines).reshape(-1, 4)
    if len(segs):
        for x1, y1, x2, y2 in segs:
            ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
            total += 1
            if ang <= 2 or abs(ang - 90) <= 2 or abs(ang - 180) <= 2:
                axis += 1
    # straight coverage: how much of the edge map lies on a detected long segment
    cov = np.zeros_like(edges)
    if len(segs):
        for x1, y1, x2, y2 in segs:
            cv2.line(cov, (x1, y1), (x2, y2), 255, thickness=3)
    straight_px = float((cv2.bitwise_and(edges, cov) > 0).sum()) / max(1, int((edges > 0).sum()))

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    flat = float((mag < 30).mean())

    quant = (img[:, :, :3] // 8).astype(np.uint8).reshape(-1, 3)
    keys = quant[:, 0].astype(np.int32) * 1024 + quant[:, 1].astype(np.int32) * 32 + quant[:, 2]
    counts = np.bincount(keys)
    colours = int((counts > 0).sum())                       # any bin touched (anti-aliasing included)
    major = int((counts >= 0.005 * keys.size).sum())        # bins holding >=0.5 % of pixels

    bg = int(np.bincount(gray.ravel()).argmax())
    ink = float((np.abs(gray.astype(int) - bg) > 25).mean())

    tmp = Path(tempfile.gettempdir()) / 'trace_tmp.svg'
    params = dict(colormode='color', hierarchical='stacked', mode='spline', filter_speckle=4,
                  color_precision=6, layer_difference=16, corner_threshold=60, length_threshold=4.0,
                  max_iterations=10, splice_threshold=45, path_precision=8)
    try:
        vtracer.convert_image_to_svg_py(str(png_path), str(tmp), **params)
    except TypeError:
        vtracer.convert_image_to_svg_py(str(png_path), str(tmp))
    paths, nodes = trace_stats(tmp)
    svg_bytes = tmp.stat().st_size

    return {
        'file': Path(png_path).name,
        'paths': paths, 'nodes': nodes, 'svg_kb': round(svg_bytes / 1024, 1),
        'nodes_per_path': round(nodes / paths, 2) if paths else None,
        'axis_pct': round(100.0 * axis / total, 1) if total else None,
        'segments': total,
        'straight_px_pct': round(100.0 * straight_px, 1),
        'flat_pct': round(100.0 * flat, 1),
        'colours_bins': colours,
        'colours_major': major,
        'ink_pct': round(100.0 * ink, 1),
        'trace_params': params,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('usage: trace_score.py image.png [--json out.json]')
    result = score(sys.argv[1])
    if '--json' in sys.argv:
        out = sys.argv[sys.argv.index('--json') + 1]
        Path(out).write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(json.dumps(result, indent=1))
