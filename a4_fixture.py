"""a4_fixture.py - A4 step 1: prove `blender -b` + Grease Pencil -> per-frame SVG.

The protocol (PROTOCOL-CAD.md, "Animation, when it arrives") needs a frame-level artefact before an
animated asset is attempted: one still per frame, in SVG, that the existing OCR / structure
toolchain can read and that carries enough identity to measure continuity between frames.

Producer half, run inside Blender's own interpreter:

    MSYS_NO_PATHCONV=1 "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" \
        -b --factory-startup -P a4_fixture.py -- --out a4-fixture

writes frames/frame-01.svg .. frame-03.svg, animation.svg, and spec.json (the declared
expectations the checker reads).  The consumer half is a4_check.py.

Two measurement findings are baked into this script, both of them surprises worth stating:

1. **`wm.grease_pencil_export_svg` ignores the scene camera.**  Re-exporting the same drawing with
   no camera, with an orthographic camera square-on to the stroke plane, and with a camera turned
   90 degrees produced byte-identical geometry.  The exporter projects through its own fixed
   oblique view, so square-on strokes arrive rotated by tens of degrees and skewed - the first
   render of this fixture was a tilted parallelogram.  The map is fixed, so it is *measured* here
   (three unit probe strokes along X, Y, Z, exported and parsed) and every point is then placed
   through the pseudo-inverse of that map.  Measured, not assumed: if Blender changes the
   projection, the fixture re-measures it and the output stays axis-aligned.
2. **The export canvas is content-relative.**  Moving the object by +2 units kept the leftmost
   output pixel at 10 px and grew the viewBox instead: the exporter crops to the drawing and pads
   10 px.  A moving element would therefore shift *every* frame's coordinate system.  The fixture
   draws a static border so the crop is identical in all frames, and the checker re-registers on a
   static element anyway so a crop change is reported rather than silently absorbed.

The scene, chosen so every property under test is knowable without looking:
  * BORDER - a static rectangle, present only to pin the export crop;
  * BLOCK  - a square that slides left to right (x centre -3, -1.5, 0);
  * END    - a square that does not move (x centre +4);
  * LINK   - a line from BLOCK's right edge to END's left edge, so it shortens by exactly BLOCK's
             motion;
  * the words START (rides above BLOCK, so it must move with it) and END (static).
Each semantic element is its own Grease Pencil layer, so the name survives into the SVG as
`layer.<name>`.  Geometry is written as explicit stroke points per frame rather than as keyframes:
the fixture must prove the *export* path frame to frame, so nothing is left for the evaluated
depsgraph to interpolate.
"""

import json
import sys
from pathlib import Path

import bpy


FRAMES = [1, 2, 3]
HALF = 1.0                       # half-size of both squares
LABEL_SIZE = 0.95                # label height, scene units
CENTER = (0.5, 0.0)              # centre of the drawing
BORDER = (13.0, 8.0)             # static border, scene units; it must contain every frame's content, because
                                 # the exporter crops to the drawing and a moving label would then shift the canvas
PROJ_SCALE = 46.0                # output pixels per scene unit
RADIUS = {'border': 0.02, 'block': 0.035, 'end': 0.035, 'link': 0.025,
          'label_start': 0.022, 'label_end': 0.022}
LAYER_ORDER = ['border', 'block', 'end', 'link', 'label_start', 'label_end']
BLOCK_X = [-3.0, -1.5, 0.0]
END_CENTRE = (4.0, 0.0)
TEXTS = {'label_start': 'START', 'label_end': 'END'}


def _arg(argv, name, default):
    if name in argv:
        return argv[argv.index(name) + 1]
    return default


# --------------------------------------------------------------------------- geometry helpers

def square(cx, cy, half):
    """Four corners plus the first again, as one closed contour."""
    pts = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]
    return pts + [pts[0]]


def _bezier(p0, p1, p2, p3, steps):
    out = []
    for i in range(steps):
        t = i / float(steps)
        u = 1.0 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def text_contours(body, steps=8):
    """Glyph outlines as closed point lists, sampled from the font curve's own beziers.

    Blender has no text primitive in Grease Pencil, and hand-drawing letters would make OCR a test
    of my own coordinates.  Converting text to Grease Pencil does work, but it hands back point
    positions whose bezier handles would then have to be re-interpreted; sampling the font curve
    directly keeps the letterforms exact and the sampling declared.  An earlier version copied
    converted positions only, losing the curves, and OCR read START as "TTART" and END as "EVD".
    """
    curve = bpy.data.curves.new('a4_font', type='FONT')
    curve.body = body
    curve.align_x = 'LEFT'
    curve.align_y = 'BOTTOM_BASELINE'
    obj = bpy.data.objects.new('a4_font', curve)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    depsgraph = bpy.context.evaluated_depsgraph_get()
    flat = obj.evaluated_get(depsgraph).to_curve(depsgraph)

    contours = []
    for spline in flat.splines:
        if spline.type != 'BEZIER' or len(spline.bezier_points) < 2:
            continue
        co = [(p.co[0], p.co[1]) for p in spline.bezier_points]
        right = [(p.handle_right[0], p.handle_right[1]) for p in spline.bezier_points]
        left = [(p.handle_left[0], p.handle_left[1]) for p in spline.bezier_points]
        contour = []
        last = len(co) - 1 if spline.use_cyclic_u else len(co) - 2
        for i in range(last + 1):
            j = (i + 1) % len(co)
            contour.extend(_bezier(co[i], right[i], left[j], co[j], steps))
        if spline.use_cyclic_u and contour:
            contour.append(contour[0])
        contours.append(contour)
    bpy.data.objects.remove(obj, do_unlink=True)
    if not contours:
        raise SystemExit('a4_fixture: %r produced no bezier splines' % body)

    xs = [x for c in contours for x, _ in c]
    ys = [y for c in contours for _, y in c]
    bottom, height = min(ys), max(ys) - min(ys)
    if height <= 0:
        raise SystemExit('a4_fixture: %r has no height' % body)
    scale = LABEL_SIZE / height
    left_edge = min(xs)
    return [[((x - left_edge) * scale, (y - bottom) * scale) for x, y in c] for c in contours], \
        (max(xs) - left_edge) * scale


def place(contours, x, y):
    return [[(x + px, y + py) for px, py in c] for c in contours]


# --------------------------------------------------------------------------- projection calibration

def _read_centres(svg_path):
    """Stroke centrelines do not survive as centrelines: the exporter writes the stroke *ribbon*,
    so each probe path is a closed outline.  The two points furthest apart lie on opposite caps,
    and because a probe has a constant radius their midpoint is the probe's centre."""
    import re
    text = svg_path.read_text(encoding='utf-8')
    out = []
    for raw in re.findall(r'<path d="([^"]*)"', text):
        nums = [float(v) for v in re.findall(r'-?\d+\.?\d*', raw)]
        pts = list(zip(nums[0::2], nums[1::2]))
        if len(pts) < 2:
            continue
        best = (0.0, pts[0], pts[0])
        for i, a in enumerate(pts):
            for b in pts[i + 1:]:
                d = (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
                if d > best[0]:
                    best = (d, a, b)
        out.append((best[1], best[2]))
    return out


def _midpoint(pair):
    a, b = pair
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


PROBE_POINTS = [(-5.5, -4.0), (0.0, -4.0), (5.5, -4.0),
                (-5.5, 0.0), (0.0, 0.0), (5.5, 0.0),
                (-5.5, 4.0), (0.0, 4.0), (5.5, 4.0)]
PROBE_LENGTH = 0.1


def measure_projection(out_dir):
    """Measure the exporter's *perspective* map from the drawing plane to SVG pixels.

    Nine short probes laid out on a grid are exported and read back; a homography is fitted to
    them, and the residual is reported.  Two properties forced this:

    * the exported view is oblique, so a square-on drawing arrives rotated;
    * it is also perspective, not affine - two identical squares placed at x = -3 and x = +4 came
      out 94.0 px and 95.6 px wide in the same file, a 1.7 % difference that a linear fit cannot
      express.

    An affine calibration was tried first and left exactly that error.  Eight parameters fitted to
    nine probes is over-determined, so the residual below is a real check on the model rather than
    a tautology; the fixture refuses to continue if the residual exceeds a tenth of a pixel.
    """
    import numpy as np

    bpy.ops.wm.read_factory_settings(use_empty=True)
    gp = bpy.data.grease_pencils.new('a4_probe')
    obj = bpy.data.objects.new('a4_probe', gp)
    bpy.context.collection.objects.link(obj)
    for i in range(len(PROBE_POINTS)):
        layer = gp.layers.new('probe%02d' % i)
        drawing = layer.frames.new(1).drawing
        drawing.add_strokes([2])
        x, y = PROBE_POINTS[i]
        for point, pos in zip(drawing.strokes[0].points, [(x, y, 0.0), (x + PROBE_LENGTH, y, 0.0)]):
            point.position = pos
            point.radius = 0.005

    probe_path = out_dir / '_calibration.svg'
    bpy.ops.wm.grease_pencil_export_svg(filepath=str(probe_path), frame_mode='ACTIVE',
                                        selected_object_type='VISIBLE', use_fill=False)
    raw = _read_centres(probe_path)
    print('A4 probe paths: %d' % len(raw))
    for i, pair in enumerate(raw[:9]):
        print('   probe %d %s -> centre %s' % (i, pair, _midpoint(pair)))
    centres = [_midpoint(raw[i]) for i in range(len(PROBE_POINTS))]
    probe_path.unlink()
    bpy.data.objects.remove(obj, do_unlink=True)

    src = np.array([[x + PROBE_LENGTH / 2.0, y] for x, y in PROBE_POINTS], dtype=float)
    dst = np.array(centres, dtype=float)

    # Normalise for conditioning; the homography is fitted in the normalised frame.
    s_scale = np.max(np.abs(src).max(axis=0))
    d_scale = np.max(np.abs(dst).max(axis=0))
    src_n, dst_n = src / s_scale, dst / d_scale

    rows = []
    for (x, y), (u, v) in zip(src_n, dst_n):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y, -u])
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.array(rows, dtype=float))
    h_n = vt[-1].reshape(3, 3)

    # Undo the normalisation.  With p_n = p / s_scale and q_n = q / d_scale, the fitted
    # q_n = H_n p_n becomes q = d_scale * H_n * (p / s_scale).
    h = np.diag([d_scale, d_scale, 1.0]) @ h_n @ np.diag([1.0 / s_scale, 1.0 / s_scale, 1.0])
    if abs(h[2, 2]) < 1e-12:
        raise SystemExit('a4_fixture: homography is degenerate')
    h = h / h[2, 2]

    projected = [(h[0, 0] * x + h[0, 1] * y + h[0, 2]) / (h[2, 0] * x + h[2, 1] * y + 1.0) for x, y in src], \
                [(h[1, 0] * x + h[1, 1] * y + h[1, 2]) / (h[2, 0] * x + h[2, 1] * y + 1.0) for x, y in src]
    residual = float(max(max(abs(u - q[0]) for u, q in zip(projected[0], dst)),
                         max(abs(v - q[1]) for v, q in zip(projected[1], dst))))
    print('A4 calibration residual: %.3f px' % residual)
    if residual > 0.1:
        raise SystemExit('a4_fixture: homography residual %.3f px is too large' % residual)
    return h.tolist(), residual


def make_projector(homography):
    """Point placer: scene (x, y) -> world point (x', y', 0) whose export lands at (s*x, -s*y).

    Inverting the measured homography is the whole trick: the drawing is laid out in a declared
    scene coordinate system, and each point is pre-warped so that Blender's perspective view puts
    it back where it belongs.
    """
    import numpy as np

    h = np.array(homography, dtype=float)
    inv = np.linalg.inv(h)

    def project(x, y):
        target = np.array([PROJ_SCALE * x, -PROJ_SCALE * y, 1.0])
        warped = inv @ target
        return (float(warped[0] / warped[2]), float(warped[1] / warped[2]), 0.0)

    return project


# --------------------------------------------------------------------------- scene

def build(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    homography, residual = measure_projection(out_dir)
    project = make_projector(homography)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = FRAMES[0], FRAMES[-1]
    scene.render.fps = 24

    ink = bpy.data.materials.new('a4_ink')
    bpy.data.materials.create_gpencil_data(ink)
    ink.grease_pencil.color = (0.0, 0.0, 0.0, 1.0)
    ink.grease_pencil.fill_color = (0.0, 0.0, 0.0, 1.0)
    ink.grease_pencil.show_fill = True

    gp = bpy.data.grease_pencils.new('a4')
    gp.materials.append(ink)
    obj = bpy.data.objects.new('a4', gp)
    bpy.context.collection.objects.link(obj)
    for name in LAYER_ORDER:
        gp.layers.new(name)

    start_contours, start_w = text_contours('START')
    end_contours, end_w = text_contours('END')

    left = CENTER[0] - BORDER[0] / 2.0
    right = CENTER[0] + BORDER[0] / 2.0
    bottom = CENTER[1] - BORDER[1] / 2.0
    top = CENTER[1] + BORDER[1] / 2.0

    for frame_no in FRAMES:
        block_x = BLOCK_X[FRAMES.index(frame_no)]
        per_layer = {
            'block': [square(block_x, 0.0, HALF)],
            'end': [square(END_CENTRE[0], END_CENTRE[1], HALF)],
            'link': [[(block_x + HALF, 0.0), (END_CENTRE[0] - HALF, END_CENTRE[1])]],
            'label_start': place(start_contours, block_x - 0.5 * start_w, HALF + 0.35),
            'label_end': place(end_contours, END_CENTRE[0] - 0.5 * end_w, HALF + 0.35),
        }
        per_layer['border'] = [[(left, bottom), (right, bottom), (right, top), (left, top), (left, bottom)]]

        for name in LAYER_ORDER:
            layer = gp.layers[name]
            drawing = layer.frames.new(frame_no).drawing
            contours = [[project(x, y) for x, y in contour] for contour in per_layer[name]]
            drawing.add_strokes([len(c) for c in contours])
            for stroke, pts in zip(drawing.strokes, contours):
                stroke.cyclic = True
                for point, pos in zip(stroke.points, pts):
                    point.position = pos
                    point.radius = RADIUS[name]

    frames_dir = out_dir / 'frames'
    frames_dir.mkdir(exist_ok=True)
    for frame_no in FRAMES:
        scene.frame_set(frame_no)
        bpy.ops.wm.grease_pencil_export_svg(
            filepath=str(frames_dir / ('frame-%02d.svg' % frame_no)),
            frame_mode='ACTIVE', selected_object_type='VISIBLE', use_fill=True)

    scene.frame_set(FRAMES[0])
    bpy.ops.wm.grease_pencil_export_svg(
        filepath=str(out_dir / 'animation.svg'),
        frame_mode='SCENE', selected_object_type='VISIBLE', use_fill=True)

    spec = {
        'declared': '2026-10-27',
        'producer': 'blender -b --factory-startup -P a4_fixture.py',
        'blender': bpy.app.version_string,
        'projection_homography_plane_to_px': homography,
        'projection_scale': PROJ_SCALE,
        'projection_residual_px': round(residual, 4),
        'frames': FRAMES,
        'layer_order': LAYER_ORDER,
        'scene_units': {'border': list(BORDER), 'centre': list(CENTER), 'square_half': HALF,
                        'label_height': LABEL_SIZE},
        'declared_geometry': {
            'block_centre_x': BLOCK_X,
            'end_centre_x': END_CENTRE[0],
            'block_width_units': 2 * HALF,
            'link_length_units': [round(END_CENTRE[0] - HALF - (bx + HALF), 6) for bx in BLOCK_X],
        },
        'labels': TEXTS,
    }
    (out_dir / 'spec.json').write_text(json.dumps(spec, indent=1), encoding='utf-8')
    print('A4 FIXTURE OK -> %s' % out_dir)
    print('A4 labels: %s' % ', '.join(sorted(TEXTS.values())))


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    out_dir = Path(_arg(argv, '--out', 'a4-fixture')).resolve()
    build(out_dir)


if __name__ == '__main__':
    main()
