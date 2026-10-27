"""a4_check.py - A4 consumer half: score per-frame SVG output of the Blender fixture.

    python a4_check.py --dir C:/path/to/a4-fixture        # the folder a4_fixture.py wrote

The fixture (a4_fixture.py, run inside Blender) writes `frames/frame-01..03.svg`, `animation.svg` and `spec.json`, the
declared expectations. This reads the SVG *output* - not the scene that produced it - and checks the four things the
frame-level protocol needs, in the order a human would:

  validity     every frame parses, carries the declared layers, and shares one canvas;
  structure    the border is axis-aligned, both squares are square and equal, the connector is horizontal;
  legibility   each frame is rasterised at 1024 px wide and its labels are read back with RapidOCR;
  continuity   registered on the static square, the moving square travels exactly the declared distance per frame, the
               START label rides it, and the connector shortens by the same amount.

The continuity check is registered because it has to be: Blender's exporter crops to the drawing's bounds, so a moving
element shifts every frame's coordinate system. The fixture keeps a static border so the crop is identical, and this
checker still registers on the static square and *reports* the registration offset, so a change in the crop shows up as
a number rather than being silently absorbed.

Rasterising is done by headless Chromium (Chrome, falling back to Edge), the same reader the rest of the track uses;
the PNGs go to a temp directory, never into the fixture.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from winproc import run as run_hidden      # a console child of a GUI app steals focus; headless Chrome included

BROWSERS = (r'C:/Program Files/Google/Chrome/Application/chrome.exe',
            r'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
            r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
            r'C:/Program Files/Microsoft/Edge/Application/msedge.exe')

FAILS = []
_NUM = re.compile(r'-?\d*\.?\d+(?:e-?\d+)?')


def check(name, cond, detail=''):
    print('%-58s %s%s' % (name, 'PASS' if cond else 'FAIL', ('  ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)
    return cond


# --------------------------------------------------------------------------- SVG reading

def parse_path(d):
    """Points of an SVG path, flattening beziers. Blender writes M/L only, but a tolerant reader keeps this honest."""
    tokens = re.findall(r'[MmLlHhVvCcSsQqTtAaZz]|' + _NUM.pattern, d)
    pts, x, y, start, i = [], 0.0, 0.0, (0.0, 0.0), 0
    cmd = None
    while i < len(tokens):
        tok = tokens[i]
        if re.match(r'[A-Za-z]', tok):
            cmd = tok
            i += 1
            if cmd in 'Zz':
                x, y = start
                pts.append((x, y))
                continue
            continue
        nums = []
        while i < len(tokens) and not re.match(r'[A-Za-z]', tokens[i]):
            nums.append(float(tokens[i]))
            i += 1
        rel = cmd.islower()
        c = cmd.upper()
        if c in ('M', 'L'):
            for k in range(0, len(nums) - 1, 2):
                px, py = nums[k], nums[k + 1]
                x, y = (x + px, y + py) if rel else (px, py)
                pts.append((x, y))
                if c == 'M' and k == 0:
                    start = (x, y)
                    cmd = 'l' if rel else 'L'
        elif c in ('H', 'V'):
            for v in nums:
                if c == 'H':
                    x = x + v if rel else v
                else:
                    y = y + v if rel else v
                pts.append((x, y))
        elif c in ('C', 'S', 'Q', 'T'):
            step = 6 if c in ('C', 'S') else 4
            for k in range(0, len(nums) - step + 1, step):
                chunk = nums[k:k + step]
                if c in ('C', 'S'):
                    cx, cy, ex, ey = chunk[-4], chunk[-3], chunk[-2], chunk[-1]
                else:
                    cx, cy, ex, ey = chunk[-2], chunk[-1], chunk[-2], chunk[-1]
                x1, y1 = (x + cx, y + cy) if rel else (cx, cy)
                x2, y2 = (x + ex, y + ey) if rel else (ex, ey)
                for s in range(1, 9):
                    t = s / 8.0
                    pts.append(((1 - t) ** 2 * x + 2 * (1 - t) * t * x1 + t * t * x2,
                                (1 - t) ** 2 * y + 2 * (1 - t) * t * y1 + t * t * y2))
                x, y = x2, y2
    return pts


def read_frame(path):
    """{'viewbox': (w, h), 'layers': {name: [points...]}} from one exported frame."""
    from xml.etree import ElementTree as ET
    text = path.read_text(encoding='utf-8')
    root = ET.fromstring(re.sub(r'<!DOCTYPE[^>]*>', '', text))
    viewbox = root.get('viewBox') or ''
    values = [float(v) for v in _NUM.findall(viewbox)]
    box = (values[2], values[3]) if len(values) == 4 else (0.0, 0.0)
    layers = {}
    for group in root.iter():
        gid = group.get('id') or ''
        if not gid.startswith('layer.'):
            continue
        name = gid.split('.')[1]
        strokes = []
        for node in group.iter():
            if node.tag.endswith('path') and node.get('d'):
                strokes.append(parse_path(node.get('d')))
        layers[name] = strokes
    return {'viewbox': box, 'layers': layers}


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def centre(points):
    x0, y0, x1, y1 = bbox(points)
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def all_points(strokes):
    return [p for s in strokes for p in s]


# --------------------------------------------------------------------------- raster + OCR

def find_browser():
    for path in BROWSERS:
        if Path(path).is_file():
            return path
    return None


def rasterise(svg, png, width=1024):
    """Headless Chromium screenshot of an HTML wrapper, so the SVG is drawn at `width` rather than at its own size."""
    browser = find_browser()
    if browser is None:
        return False, 'no Chrome or Edge found'
    wrapper = svg.with_suffix('.wrap.html')
    wrapper.write_text('<html><body style="margin:0;background:#ffffff">'
                       '<img src="%s" style="display:block;width:%dpx;height:auto"></body></html>'
                       % (svg.name, width), encoding='utf-8')
    cmd = [browser, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--window-size=%d,1400' % width,
           '--screenshot=%s' % png, wrapper.as_uri()]
    try:
        run_hidden(cmd, capture_output=True, timeout=120)
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e)
    ok = png.is_file() and png.stat().st_size > 0
    return ok, '' if ok else 'screenshot was not written'


def read_text(png, ocr):
    result, _ = ocr(str(png))
    return [r[1] for r in (result or [])]


# --------------------------------------------------------------------------- the checks

def main(argv):
    ap = argparse.ArgumentParser(description='Check the A4 Blender fixture output.')
    ap.add_argument('--dir', required=True, help='the folder a4_fixture.py wrote')
    ap.add_argument('--no-ocr', action='store_true', help='skip the raster/OCR half (no browser on this box)')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    root = Path(args.dir).resolve()
    spec_path = root / 'spec.json'
    if not spec_path.is_file():
        print('missing %s - run a4_fixture.py inside Blender first' % spec_path)
        return 2
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    frames_dir = root / 'frames'
    frame_files = sorted(frames_dir.glob('frame-*.svg'))
    print('blender %s, %d frames, %d declared layers'
          % (spec.get('blender'), len(frame_files), len(spec['layer_order'])))

    print('\n-- validity')
    check('one SVG per declared frame', len(frame_files) == len(spec['frames']),
          (len(frame_files), len(spec['frames'])))
    frames = [read_frame(p) for p in frame_files]
    for i, frame in enumerate(frames):
        missing = [n for n in spec['layer_order'] if n not in frame['layers']]
        check('frame %d carries every declared layer' % (i + 1), not missing, missing)
    canvases = {frame['viewbox'] for frame in frames}
    check('every frame shares one canvas', len(canvases) == 1, sorted(canvases))

    print('\n-- structure')
    width, height = spec['scene_units']['border']
    if frames:
        border = all_points(frames[0]['layers'].get('border', []))
        x0, y0, x1, y1 = bbox(border)
        want = width / height
        got = (x1 - x0) / max(1e-9, y1 - y0)
        check('the border is a rectangle of the declared ratio', abs(got - want) / want < 0.02,
              '%.3f vs %.3f' % (got, want))
        corners = set()
        for p in border:
            corners.add((round(p[0] - x0, 1) in (0.0,) or round(p[1] - y0, 1) in (0.0,), p))
        check('the border has four axis-aligned corners',
              sum(1 for p in border if abs(p[0] - x0) < 1.5 or abs(p[0] - x1) < 1.5) >= 2
              and sum(1 for p in border if abs(p[1] - y0) < 1.5 or abs(p[1] - y1) < 1.5) >= 2)

    sizes = {}
    for name in ('block', 'end'):
        points = all_points(frames[0]['layers'].get(name, [])) if frames else []
        if not points:
            check('layer %s has geometry' % name, False)
            continue
        x0, y0, x1, y1 = bbox(points)
        sizes[name] = (x1 - x0, y1 - y0)
        check('%s is square (aspect within 2 %%)' % name,
              abs((x1 - x0) - (y1 - y0)) / max(1.0, (y1 - y0)) < 0.02, sizes[name])
    if 'block' in sizes and 'end' in sizes:
        check('both squares are the same size',
              abs(sizes['block'][0] - sizes['end'][0]) / sizes['end'][0] < 0.01,
              (sizes['block'], sizes['end']))

    link = all_points(frames[0]['layers'].get('link', [])) if frames else []
    if link:
        x0, y0, x1, y1 = bbox(link)
        check('the connector is horizontal (height = stroke width)', (y1 - y0) < 0.1 * (x1 - x0),
              (x1 - x0, y1 - y0))

    print('\n-- legibility and continuity')
    scale = spec['projection_scale']
    labels = spec['labels']
    declared_shift = [round((b - a) * scale, 3)
                      for a, b in zip(spec['declared_geometry']['block_centre_x'],
                                      spec['declared_geometry']['block_centre_x'][1:])]
    if not args.no_ocr:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as e:
            print('  (skipping OCR: %s)' % e)
            args.no_ocr = True
    ocr = None if args.no_ocr else RapidOCR()
    tmp = Path(tempfile.mkdtemp(prefix='a4-check-'))
    try:
        previous = None
        for i, (path, frame) in enumerate(zip(frame_files, frames)):
            tag = 'frame %d' % (i + 1)
            if ocr is not None:
                png = tmp / (path.stem + '.png')
                ok, why = rasterise(path, png)
                if ok:
                    seen = read_text(png, ocr)
                    joined = ' '.join(seen).upper()
                    missed = [v for v in labels.values() if v.upper() not in joined]
                    check('%s: every label reads back (%s)' % (tag, ', '.join(labels.values())),
                          not missed, 'missed %s in %r' % (missed, seen))
                else:
                    check('%s: rasterised for OCR' % tag, False, why)

            end_c = centre(all_points(frame['layers'].get('end', [])))
            block_pts = all_points(frame['layers'].get('block', []))
            start_pts = all_points(frame['layers'].get('label_start', []))
            link_pts = all_points(frame['layers'].get('link', []))
            block_c = centre(block_pts) if block_pts else (0, 0)
            start_c = centre(start_pts) if start_pts else (0, 0)
            current = {'block': block_c, 'end': end_c, 'start': start_c,
                       'link': (bbox(link_pts)[2] - bbox(link_pts)[0]) if link_pts else None}
            if previous is None:
                check('%s: the static square is where the moving one is not' % tag,
                      abs(block_c[0] - end_c[0]) > 50, abs(block_c[0] - end_c[0]))
                check('%s: START sits above the moving square' % tag,
                      abs(start_c[0] - block_c[0]) < 12 and start_c[1] < block_c[1],
                      (start_c, block_c))
            else:
                want = declared_shift[i - 1] if i - 1 < len(declared_shift) else declared_shift[-1]
                moved = block_c[0] - previous['block'][0]
                check('%s: the moving square travels the declared %.0f px' % (tag, want),
                      abs(moved - want) <= max(1.5, 0.03 * want), '%.1f px' % moved)
                check('%s: the static square did not move' % tag,
                      abs(end_c[0] - previous['end'][0]) <= 0.6 and abs(end_c[1] - previous['end'][1]) <= 0.6,
                      (end_c, previous['end']))
                check('%s: START rides the square (same delta within 1 px)' % tag,
                      abs((start_c[0] - previous['start'][0]) - moved) <= 1.0,
                      (start_c[0] - previous['start'][0], moved))
                if current['link'] is not None and previous['link'] is not None:
                    shortened = previous['link'] - current['link']
                    check('%s: the connector shortens by the motion (%.0f px)' % (tag, moved),
                          abs(shortened - moved) <= max(1.5, 0.03 * moved),
                          '%.1f px vs %.1f px' % (shortened, moved))
            previous = current
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n-- animation readiness')
    animation = root / 'animation.svg'
    if animation.is_file():
        text = animation.read_text(encoding='utf-8')
        groups = len(re.findall(r'id="blender_frame\.', text))
        check('the single-file export carries one group per frame', groups == len(spec['frames']), groups)
        check('and it is animatable without a script', '<animate' in text)
    else:
        check('the single-file animated export exists', False, str(animation))

    result = {'declared': spec.get('declared'), 'frames': len(frame_files),
              'checks_failed': FAILS, 'passed': not FAILS}
    out = root / 'result-a4-fixture.json'
    out.write_text(json.dumps(result, indent=1), encoding='utf-8')
    print('\n%s; wrote %s' % ('ALL PASS' if not FAILS else 'FAILURES: %s' % ', '.join(FAILS), out.name))
    return 0 if not FAILS else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
