"""raster_measure.py - the one implementation of the raster diagram metrics.

Both `trace_score.py` (per image, CLI) and `score_arm.py` (per folder) call `measure()`, so a
number quoted in a results file and a number quoted from this module cannot drift apart.

Metrics, all local and deterministic:

  width, height          : pixel dimensions of the file as read
  ink_pct                : share of pixels away from the modal background value (>25 grey levels).
                           Measured on the *analysis* image (see low_contrast_normalised).
  colours_major          : 5-bit colour bins holding >= 0.5 % of the pixels. **This is the gradient
                           detector.** A flat-fill diagram has 2-4; a smooth gradient has many,
                           because every band of the ramp is its own bin.
  colours_bins           : every 5-bit bin touched at all (anti-aliasing included) - a noise read.
  edge_sparse_pct        : share of pixels WITHOUT a sharp local edge (Sobel magnitude < 30),
                           computed on the *original* greyscale image. This is an edge-sparsity
                           measure, not a gradient detector - a smooth ramp is edge-sparse (93.6 %
                           on a synthetic gradient) and a line diagram is slightly more so (97.7 %
                           on a flow diagram), which is exactly why the two numbers cannot be read
                           as "amount of gradient". Use colours_major for that question.
  segments               : straight segments found by probabilistic Hough on the analysis edges
  axis_pct               : share of those segments within 2 degrees of horizontal/vertical
  straight_px_pct        : share of edge pixels lying on a detected segment
  low_contrast_normalised: True when the stroke/background separation was below what the edge
                           detector can act on and the image was stretched before edge detection.
                           A metric rescued this way is *reported*, not silently earned: the reader
                           can discount or reject the row.

Why the contrast rescue exists: a KiCad plot draws F.SilkS in pale yellow `#F2EDA1` on white.
`cv2.Canny(gray, 50, 150)` found **zero** straight segments on that raster while OCR read the text
fine (RESULTS-METHOD-E.md), so the trace metric was reporting the exporter's colour choice as a
geometry failure. With the rescue the same raster yields its real segments and the JSON says so.

Usage:
    import raster_measure
    raster_measure.measure('render/flow-1.png')

Run through cmd.exe: .venv\\Scripts\\python.exe raster_measure.py image.png
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# Edge detection, unchanged from the original trace_score.py so old numbers stay comparable.
CANNY_LO, CANNY_HI = 50, 150
EDGE_SPARSE_MAG = 30          # Sobel magnitude below which a pixel counts as "no sharp edge"
HOUGH = dict(threshold=40, minLineLength=40, maxLineGap=3)
# Stroke/background separation (grey levels) that Canny(50,150) can act on at all.
STROKE_FLOOR = 60.0
# The rescue only applies to stroke-on-background images. A gradient field has no dominant
# background value, and stretching it would manufacture edges that are not in the drawing.
MIN_BG_FRACTION = 0.30


def load_bgr(png_path):
    """Read a PNG, compositing any alpha over white. Returns (bgr uint8, gray uint8)."""
    img = cv2.imread(str(png_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise IOError('unreadable image: %s' % png_path)
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3:4] / 255.0
        img = (img[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)
    if img.ndim == 2:
        gray = img
    else:
        gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    return img, gray


def stroke_contrast(gray):
    """(contrast, background_value, background_fraction).

    contrast is the 99th percentile of |pixel - background| over pixels that differ from the
    background at all: the separation between the page and its strongest ink. It is *not* a local
    gradient, so a smooth ramp does not read as high contrast merely by being smooth - but its
    background fraction is small, which is what keeps the rescue away from gradients.
    """
    counts = np.bincount(gray.ravel(), minlength=256)
    bg = int(counts.argmax())
    bg_fraction = float(counts[bg]) / float(gray.size)
    dev = np.abs(gray.astype(np.int16) - bg).astype(np.float32)
    ink = dev[dev > 2]
    if ink.size < max(16, int(0.0005 * dev.size)):
        return 0.0, bg, bg_fraction
    return float(np.percentile(ink, 99.0)), bg, bg_fraction


def normalise_ink(gray, bg, contrast):
    """Min-max stretch of the ink mask: background -> 255, strongest ink -> 0.

    `dev` is unsigned, so this works whether the ink is darker (black strokes) or lighter
    (#F2EDA1 on white) than the page. Signed polarity is irrelevant to Canny.
    """
    dev = np.abs(gray.astype(np.float32) - bg)
    scale = 255.0 / max(contrast, 1.0)
    return np.clip(255.0 - dev * scale, 0, 255).astype(np.uint8)


def _segments(edges):
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, **HOUGH)
    if lines is None:
        return np.zeros((0, 4), dtype=np.int32)
    return np.asarray(lines).reshape(-1, 4)


def measure(png_path):
    """All raster metrics for one image, as a plain dict (percentages already x100)."""
    img, gray = load_bgr(png_path)
    h, w = gray.shape

    contrast, bg, bg_fraction = stroke_contrast(gray)
    low_contrast = bool(contrast < STROKE_FLOOR and bg_fraction >= MIN_BG_FRACTION and contrast > 1.0)
    analysis = normalise_ink(gray, bg, contrast) if low_contrast else gray

    edges = cv2.Canny(analysis, CANNY_LO, CANNY_HI)
    segs = _segments(edges)
    axis = 0
    for x1, y1, x2, y2 in segs:
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
        if ang <= 2 or abs(ang - 90) <= 2 or abs(ang - 180) <= 2:
            axis += 1
    total = int(len(segs))

    # straight coverage: how much of the edge map lies on a detected long segment
    cov = np.zeros_like(edges)
    for x1, y1, x2, y2 in segs:
        cv2.line(cov, (x1, y1), (x2, y2), 255, thickness=3)
    straight_px = float((cv2.bitwise_and(edges, cov) > 0).sum()) / max(1, int((edges > 0).sum()))

    # Edge sparsity is measured on the ORIGINAL image: it answers "how little hard edge is in this
    # picture", so it must not inherit the rescue that the segment count may have needed.
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    edge_sparse = float((mag < EDGE_SPARSE_MAG).mean())

    bgr = img if (img.ndim == 3) else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    quant = (bgr[:, :, :3] // 8).astype(np.uint8).reshape(-1, 3)
    keys = quant[:, 0].astype(np.int32) * 1024 + quant[:, 1].astype(np.int32) * 32 + quant[:, 2]
    counts = np.bincount(keys)
    colours = int((counts > 0).sum())                       # any bin touched (anti-aliasing included)
    major = int((counts >= 0.005 * keys.size).sum())        # bins holding >=0.5 % of pixels

    ink = float((np.abs(analysis.astype(int) - int(np.bincount(analysis.ravel(), minlength=256).argmax())) > 25).mean())

    return {
        'width': int(w), 'height': int(h),
        'segments': total,
        'axis_pct': round(100.0 * axis / total, 1) if total else None,
        'axis_segments': axis,
        'straight_px_pct': round(100.0 * straight_px, 1),
        'edge_sparse_pct': round(100.0 * edge_sparse, 1),
        'colours_bins': colours,
        'colours_major': major,
        'ink_pct': round(100.0 * ink, 1),
        'stroke_contrast': round(contrast, 1),
        'bg_luma': int(bg),
        'bg_fraction': round(bg_fraction, 3),
        'low_contrast_normalised': low_contrast,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('usage: raster_measure.py image.png')
    print(json.dumps(measure(sys.argv[1]), indent=1))
