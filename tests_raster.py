"""tests_raster.py - executable self-test for raster_measure.py (script, not pytest).

Run:  .venv\\Scripts\\python.exe tests_raster.py
Exit code 0 only if every assertion passes; prints PASS/FAIL per assertion.

Fixtures are generated into `fixtures_raster/` on every run (deterministic, no network, no model):

  a-diagram.png    axis-aligned black line diagram on white      KNOWN-GOOD  20+ segments, all H/V
  a-black-style.png the same drawing at 2 px anti-aliased stroke  KNOWN-GOOD  the `*-black` render style
  b-gradient.png   soft radial ramp, white centre -> dark edge    KNOWN-BAD   many colours, no segments
  c-pale.png       the same diagram in #f2eda1 on #ffffff         KNOWN-BAD   zero edge pixels to
                                                                  Canny(50,150) - the Method E failure
  d-blank.png      pure white                                     KNOWN-BAD   nothing to find

The pale fixture is the regression this package exists for: RESULTS-METHOD-E.md records a KiCad plot
of F.SilkS in pale yellow on white yielding **0 straight segments twice** while OCR read the text.
`prefix_segments()` below reproduces the old detector exactly, so the test asserts BOTH that the old
path finds nothing (the failure is real and still reproducible) AND that the shipped path finds the
geometry - and only the pale row may claim `low_contrast_normalised` for it.
"""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import raster_measure as rm  # noqa: E402
import trace_score  # noqa: E402

GEN = ROOT / 'fixtures_raster'
W = H = 1024
PALE = '#f2eda1'

failures = []
checks = []


def check(label, condition, detail):
    ok = bool(condition)
    checks.append(ok)
    print('%-4s %-62s %s' % ('PASS' if ok else 'FAIL', label, detail))
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------- fixtures
def _bgr(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))[::-1]


def draw_diagram(colour, thickness, aa=False):
    """Four stacked boxes joined by three vertical connectors, plus a rule and a legend box.
    Every stroke is horizontal or vertical: axis_pct has an exact expected value of 100."""
    img = np.full((H, W, 3), 255, np.uint8)
    line = cv2.LINE_AA if aa else 8
    c = _bgr(colour)
    boxes = [(362, 60, 662, 180), (362, 290, 662, 410), (362, 520, 662, 640), (362, 750, 662, 870)]
    for x0, y0, x1, y1 in boxes:
        cv2.rectangle(img, (x0, y0), (x1, y1), c, thickness, line)
    for a, b in zip(boxes, boxes[1:]):
        xm = (a[0] + a[2]) // 2
        cv2.line(img, (xm, a[3]), (xm, b[1]), c, thickness, line)
    cv2.line(img, (120, 960), (900, 960), c, thickness, line)
    cv2.rectangle(img, (120, 460), (240, 700), c, thickness, line)
    return img


def build_fixtures():
    GEN.mkdir(exist_ok=True)
    cv2.imwrite(str(GEN / 'a-diagram.png'), draw_diagram('#000000', 5))
    cv2.imwrite(str(GEN / 'a-black-style.png'), draw_diagram('#000000', 2, aa=True))
    cv2.imwrite(str(GEN / 'c-pale.png'), draw_diagram(PALE, 2, aa=True))
    cv2.imwrite(str(GEN / 'd-blank.png'), np.full((H, W, 3), 255, np.uint8))

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx = cy = (W - 1) / 2.0
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    ramp = (255.0 - 225.0 * (r / r.max())).astype(np.uint8)
    cv2.imwrite(str(GEN / 'b-gradient.png'), np.dstack([ramp, ramp, ramp]))


def prefix_segments(png):
    """The detector exactly as shipped before this package: Canny on the raw greyscale, same Hough.
    Kept verbatim so the 'known-bad' half of the test measures the old behaviour, not a strawman."""
    _, gray = rm.load_bgr(png)
    edges = cv2.Canny(gray, rm.CANNY_LO, rm.CANNY_HI)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40, minLineLength=40, maxLineGap=3)
    return 0 if lines is None else len(np.asarray(lines).reshape(-1, 4))


REQUIRED_KEYS = ('width', 'height', 'ink_pct', 'colours_major', 'edge_sparse_pct',
                 'segments', 'axis_pct', 'low_contrast_normalised')


def main():
    build_fixtures()
    m = {name: rm.measure(GEN / ('%s.png' % name)) for name in
         ('a-diagram', 'a-black-style', 'b-gradient', 'c-pale', 'd-blank')}

    # -- contract -------------------------------------------------------
    missing = [k for k in REQUIRED_KEYS if k not in m['a-diagram']]
    check('measure() exposes the documented keys', not missing,
          'missing=%s' % missing if missing else '8/8 present')
    check('dimensions are reported from the file', (m['a-diagram']['width'], m['a-diagram']['height']) == (W, H),
          '%dx%d' % (m['a-diagram']['width'], m['a-diagram']['height']))

    # (a) synthetic axis-aligned diagram -- KNOWN-GOOD
    a = m['a-diagram']
    check('a-diagram: axis_pct >= 90', a['axis_pct'] is not None and a['axis_pct'] >= 90, 'axis_pct=%s' % a['axis_pct'])
    check('a-diagram: segments >= 20', a['segments'] >= 20, 'segments=%d' % a['segments'])
    check('a-diagram: not claimed as a contrast rescue', a['low_contrast_normalised'] is False,
          'low_contrast_normalised=%s stroke_contrast=%.1f' % (a['low_contrast_normalised'], a['stroke_contrast']))

    # black-twin render style (`render/kicad-llm-flow-1-black.png`-style content)
    ab = m['a-black-style']
    check('a-black-style: segments >= 20', ab['segments'] >= 20, 'segments=%d' % ab['segments'])
    check('a-black-style: axis_pct >= 90', ab['axis_pct'] is not None and ab['axis_pct'] >= 90, 'axis_pct=%s' % ab['axis_pct'])

    # (b) soft radial gradient -- KNOWN-BAD for tracing, KNOWN-GOOD for colours_major
    b = m['b-gradient']
    check('b-gradient: colours_major >= 10 (gradient detector fires)', b['colours_major'] >= 10,
          'colours_major=%d' % b['colours_major'])
    check('b-gradient: segments <= 6 (no invented geometry)', b['segments'] <= 6, 'segments=%d' % b['segments'])
    check('b-gradient: not stretched (gradient is not a stroke image)', b['low_contrast_normalised'] is False,
          'bg_fraction=%.3f' % b['bg_fraction'])

    # (c) pale #f2eda1 on #ffffff -- the Method E failure, and its rescue
    old = prefix_segments(GEN / 'c-pale.png')
    check('c-pale: OLD detector finds 0 segments (failure reproduced)', old == 0, 'segments=%d' % old)
    c = m['c-pale']
    check('c-pale: segments >= 20 after the rescue', c['segments'] >= 20, 'segments=%d' % c['segments'])
    check('c-pale: low_contrast_normalised == True (rescue is reported)', c['low_contrast_normalised'] is True,
          'stroke_contrast=%.1f bg_luma=%d' % (c['stroke_contrast'], c['bg_luma']))
    check('c-pale: axis_pct >= 90 (geometry, not noise)', c['axis_pct'] is not None and c['axis_pct'] >= 90,
          'axis_pct=%s' % c['axis_pct'])
    delta = abs(c['segments'] - ab['segments']) / float(max(1, ab['segments']))
    check('c-pale matches its black twin within 20 % of segments', delta <= 0.20,
          'pale=%d black=%d delta=%.1f%%' % (c['segments'], ab['segments'], 100 * delta))

    # blank page -- nothing may be invented
    d = m['d-blank']
    check('d-blank: segments == 0', d['segments'] == 0, 'segments=%d' % d['segments'])
    check('d-blank: colours_major == 1', d['colours_major'] == 1, 'colours_major=%d' % d['colours_major'])
    check('d-blank: ink_pct == 0', d['ink_pct'] == 0.0, 'ink_pct=%s' % d['ink_pct'])

    # -- the numbers the docstring cites, on the repo's own fixtures --------
    for name, flat, segs, major in (('synthetic-flow.png', 97.7, 50, 2), ('synthetic-gradient.png', 93.6, 4, 30)):
        p = ROOT / name
        if not p.exists():
            check('%s present' % name, False, 'missing - cannot verify the quoted values')
            continue
        v = rm.measure(p)
        check('%s: edge_sparse_pct == %.1f (unchanged)' % (name, flat), v['edge_sparse_pct'] == flat,
              'edge_sparse_pct=%s' % v['edge_sparse_pct'])
        check('%s: segments == %d' % (name, segs), v['segments'] == segs, 'segments=%d' % v['segments'])
        check('%s: colours_major == %d' % (name, major), v['colours_major'] == major,
              'colours_major=%d' % v['colours_major'])

    # -- one implementation: trace_score must not recompute its own -------
    t = trace_score.score(GEN / 'c-pale.png')
    check('trace_score.score() delegates to measure()', t['edge_sparse_pct'] == c['edge_sparse_pct']
          and t['segments'] == c['segments'] and t['axis_pct'] == c['axis_pct'],
          'trace_score segs=%d sparse=%s vs measure segs=%d sparse=%s'
          % (t['segments'], t['edge_sparse_pct'], c['segments'], c['edge_sparse_pct']))
    check('trace_score keeps flat_pct as the same number', t['flat_pct'] == t['edge_sparse_pct'],
          'flat_pct=%s edge_sparse_pct=%s' % (t['flat_pct'], t['edge_sparse_pct']))
    check('trace_score marks the rescued row', t['low_contrast_normalised'] is True,
          'low_contrast_normalised=%s' % t['low_contrast_normalised'])

    print('\n%d assertion(s), %d failure(s)' % (len(checks), len(failures)))
    if failures:
        print('FAILURES: %s' % ', '.join(failures))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
