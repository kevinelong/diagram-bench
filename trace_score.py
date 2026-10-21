"""trace_score.py - score one raster for the vector-traceability job.

The raster metrics themselves live in `raster_measure.py` and are called from there, so a number in
a results file and a number from `raster_measure.measure()` cannot drift apart. This module adds the
tracing economy metrics (vtracer) on top.

Metrics:
  width, height     : pixel dimensions of the file
  paths, nodes      : vtracer SVG path count and total path-command count (economy)
  axis_pct          : share of detected straight segments within 2 degrees of H/V
  segments          : how many straight segments Hough found at all
  straight_px_pct   : share of edge pixels explained by long straight segments
  edge_sparse_pct   : share of pixels WITHOUT a sharp local edge (Sobel magnitude < 30), measured on
                      the original greyscale. This is edge sparsity, NOT a gradient detector: a
                      smooth ramp measures 93.6 % and a line diagram 97.7 % on the repo's own
                      fixtures, so a high value does not mean "mostly flat colour". Use
                      `colours_major` for the gradient question.
  flat_pct          : legacy alias of edge_sparse_pct, same value, kept so older result files and
                      readers still resolve. New code should read edge_sparse_pct.
  colours_major     : 5-bit colour bins holding >= 0.5 % of pixels - **the gradient detector**
                      (a flat-fill diagram: 2-4; a smooth gradient: 20-30+)
  colours_bins      : every 5-bit bin touched at all (anti-aliasing included)
  ink_pct           : share of non-background pixels (a blank image is not a good sign), measured on
                      the analysis image
  low_contrast_normalised : True when the stroke/background separation was too low for edge
                      detection and the image was stretched first. Pale-yellow-on-white plots (the
                      KiCad F.SilkS export) are the case this exists for: without the rescue they
                      found zero segments while OCR read the text fine. A rescued row is marked, so
                      a reader can discount it rather than be told the geometry was measured.

Usage: trace_score.py image.png [--json out.json]
Run through cmd.exe: .venv\\Scripts\\python.exe trace_score.py image.png
"""
import json
import sys
import tempfile
from pathlib import Path

import vtracer

import raster_measure


def trace_stats(svg_path):
    text = Path(svg_path).read_text(encoding='utf-8', errors='replace')
    paths = text.count('<path')
    nodes = text.count('C') + text.count('L') + text.count('M') + text.count('Q')
    return paths, nodes


def score(png_path, measured=None):
    """Trace economy + the raster metrics. `measured` lets a caller pass an existing
    raster_measure.measure() result so a folder pass measures each image exactly once."""
    try:
        m = measured if measured is not None else raster_measure.measure(png_path)
    except IOError:
        return {'error': 'unreadable'}

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
        'width': m['width'], 'height': m['height'],
        'paths': paths, 'nodes': nodes, 'svg_kb': round(svg_bytes / 1024, 1),
        'nodes_per_path': round(nodes / paths, 2) if paths else None,
        'axis_pct': m['axis_pct'],
        'segments': m['segments'],
        'straight_px_pct': m['straight_px_pct'],
        'edge_sparse_pct': m['edge_sparse_pct'],
        'flat_pct': m['edge_sparse_pct'],          # legacy alias, see module docstring
        'colours_bins': m['colours_bins'],
        'colours_major': m['colours_major'],
        'ink_pct': m['ink_pct'],
        'stroke_contrast': m['stroke_contrast'],
        'low_contrast_normalised': m['low_contrast_normalised'],
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
