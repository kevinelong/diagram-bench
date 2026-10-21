"""post_outline.py - add an Edge.Cuts rectangle around whatever a `pcbnew` script drew.

Why this exists, all measured (RESULTS-METHOD-E.md, RESULTS-METHOD-E-LLM.md):

  * `kicad-cli pcb export svg --page-size-mode 2` fits the **board outline**. With no Edge.Cuts geometry there is
    no board area to fit, so the page is exported instead and the drawing becomes a speck in a corner.
  * `--exclude-drawing-sheet` is required as well: without it the export carries a 33-group drawing frame with 23
    extra `<text>` elements, which corrupts every text-node count.
  * Cropping the viewBox in the SVG afterwards is NOT a fix: KiCad path data mixes relative and absolute commands
    (`M10 8 L10 13` next to `M 0.2,0.2 20.2,0.2`), so an `[ML]x,y` regex measures the wrong box. The crop belongs
    on the board, before export, where the geometry is still exact millimetres.

So: read the board, take the bounding box of every drawing primitive the script placed, and write one
`gr_rect` on Edge.Cuts around it. Re-running is idempotent - any existing Edge.Cuts drawing is replaced.

API
    outline(pcb_text, pad=1.0)              -> (text_with_outline, box dict)   pure string work, no KiCad needed
    readings(pcb_text)                      -> list of parsed drawing dicts
    add_outline(pcb_path, out_path, pad)    -> box dict (also writes the file)
    export_svg(pcb_path, svg_path)          -> (returncode, stderr tail)

CLI
    python post_outline.py <board.kicad_pcb> [--out board2.kicad_pcb] [--pad 1.0] [--export out.svg] [--json]

The module is importable with plain CPython - it never imports `pcbnew`, so the arm can call it from the .venv
interpreter while the model's own script runs under KiCad's interpreter.
"""
import argparse
import json
import math
import re
import subprocess
import sys
import uuid
from pathlib import Path

KICAD_CLI = r'C:\Users\kevin\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe'
EDGE = 'Edge.Cuts'


# ---------------------------------------------------------------- S-expression scanning

def _blocks(text):
    """Yield (keyword, start, end) for every block that opens at depth 2, i.e. a direct child of (kicad_pcb ...).

    A real scanner rather than a regex: `gr_text "A (B"` puts parentheses inside strings, and a balanced-paren
    regex would split the file at the wrong place. `end` is one past the closing paren.
    """
    out = []
    depth = 0
    i = 0
    n = len(text)
    start = None
    kw = None
    while i < n:
        c = text[i]
        if c == '"':                                   # skip a quoted string, honouring \\ and \" escapes
            i += 1
            while i < n:
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == '"':
                    break
                i += 1
            i += 1
            continue
        if c == '(':
            depth += 1
            if depth == 2:
                m = re.match(r'\(\s*([A-Za-z0-9_.\-]+)', text[i:])
                kw = m.group(1) if m else None
                start = i
            i += 1
            continue
        if c == ')':
            if depth == 2 and start is not None:
                out.append((kw, start, i + 1))
                start = None
                kw = None
            depth -= 1
            i += 1
            continue
        i += 1
    return out


_NUM = r'(-?[\d.]+)'


def _pairs(block, key):
    """All coordinate pairs of `(key x y ...)` inside one block."""
    return [(float(a), float(b)) for a, b in
            re.findall(r'\(\s*%s\s+%s\s+%s' % (key, _NUM, _NUM), block)]


def _layer(block):
    m = re.search(r'\(\s*layer\s+(?:"([^"]*)"|([A-Za-z0-9_.\-]+))', block)
    return (m.group(1) or m.group(2)) if m else ''


def _string(block, key):
    m = re.search(r'\(\s*%s\s+"((?:[^"\\]|\\.)*)"' % key, block)
    return m.group(1) if m else None


def readings(pcb_text):
    """Parse every top-level drawing into {kind, layer, coords, text, size, rot} - enough for a bounding box."""
    items = []
    for kw, s, e in _blocks(pcb_text):
        if not kw or not kw.startswith('gr_'):
            continue
        block = pcb_text[s:e]
        pts = []
        for key in ('start', 'end', 'center', 'mid', 'xy'):
            pts += _pairs(block, key)
        at = re.findall(r'\(\s*at\s+%s\s+%s(?:\s+%s)?' % (_NUM, _NUM, _NUM), block)
        center = None
        rot = 0.0
        if at:
            x, y = float(at[0][0]), float(at[0][1])
            rot = float(at[0][2]) if at[0][2] else 0.0
            center = (x, y)
        size = None
        m = re.search(r'\(\s*size\s+%s\s+%s' % (_NUM, _NUM), block)
        if m:
            size = (float(m.group(1)), float(m.group(2)))       # (height, width) in mm
        items.append({'kind': kw, 'layer': _layer(block), 'coords': pts, 'at': center,
                      'rot': rot, 'text': _string(block, kw), 'size': size})
    return items


def _text_corners(x, y, rot_deg, text, size):
    """Four corners of a centred board text, rotated. KiCad stroke text advances ~1.0 em per character."""
    if not text:
        return []
    h, w = size if size else (1.0, 1.0)
    half_w = 0.5 * max(1, len(text)) * w * 1.1           # 1.1 em per glyph: generous, never clips a label
    half_h = 0.5 * h
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for dx, dy in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
        out.append((x + dx * ca - dy * sa, y + dx * sa + dy * ca))
    return out


def bbox(items, drop_layer=EDGE):
    """Bounding box of everything not on `drop_layer`, so a second run measures the drawing and not our own rect."""
    xs, ys, shapes, texts = [], [], 0, 0
    for it in items:
        if it['layer'] == drop_layer:
            continue
        pts = list(it['coords'])
        if it['at']:
            pts.append(it['at'])
        if it['kind'] == 'gr_text':
            if it['at']:
                pts += _text_corners(it['at'][0], it['at'][1], it['rot'], it['text'], it['size'])
            texts += 1
        else:
            shapes += 1
        for x, y in pts:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return {'x0': min(xs), 'y0': min(ys), 'x1': max(xs), 'y1': max(ys), 'shapes': shapes, 'texts': texts,
            'drawings': shapes + texts}


def _fmt(v):
    s = ('%.4f' % v).rstrip('0').rstrip('.')
    return s if s not in ('', '-0') else '0'


def outline(pcb_text, pad=1.0):
    """Return (new_text, box). Any existing Edge.Cuts drawing is removed first, so this is idempotent."""
    blocks = _blocks(pcb_text)
    drop = [(s, e) for (kw, s, e) in blocks
            if kw and kw.startswith('gr_') and _layer(pcb_text[s:e]) == EDGE]
    body = pcb_text
    for s, e in sorted(drop, reverse=True):                      # cut from the end so offsets stay valid
        body = body[:s] + body[e:]
    box = bbox(readings(body))
    if not box:
        return body, {'outline_mm': None, 'shapes': 0, 'texts': 0, 'drawings': 0}
    x0, y0 = box['x0'] - pad, box['y0'] - pad
    x1, y1 = box['x1'] + pad, box['y1'] + pad
    rect = ('\t(gr_rect\n'
            '\t\t(start %s %s)\n'
            '\t\t(end %s %s)\n'
            '\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n'
            '\t\t(fill no)\n'
            '\t\t(layer "%s")\n'
            '\t\t(uuid "%s")\n'
            '\t)\n') % (_fmt(x0), _fmt(y0), _fmt(x1), _fmt(y1), EDGE, uuid.uuid4())
    stripped = body.rstrip()
    cut = stripped.rfind(')')                                    # the closing paren of (kicad_pcb ...)
    new_text = stripped[:cut] + rect + stripped[cut:] + '\n'
    box = dict(box)
    box.update({'x0': round(x0, 3), 'y0': round(y0, 3), 'x1': round(x1, 3), 'y1': round(y1, 3),
                'w': round(x1 - x0, 3), 'h': round(y1 - y0, 3), 'pad': pad,
                'outline_mm': [round(x1 - x0, 2), round(y1 - y0, 2)]})
    return new_text, box


def add_outline(pcb_path, out_path=None, pad=1.0):
    """Add the Edge.Cuts rectangle to `pcb_path` (in place unless `out_path`). Returns the measured box."""
    src = Path(pcb_path)
    new_text, box = outline(src.read_text(encoding='utf-8'), pad=pad)
    Path(out_path or src).write_text(new_text, encoding='utf-8')
    return box


def export_svg(pcb_path, svg_path, layers='F.SilkS', mode_single=True):
    """`--mode-single` needs a FILE path (an -o directory silently writes nothing) and --exclude-drawing-sheet
    is required, or the page frame adds ~23 stray <text> elements to every count."""
    cmd = [KICAD_CLI, 'pcb', 'export', 'svg']
    if mode_single:
        cmd += ['--mode-single']
    cmd += ['--layers', layers, '--exclude-drawing-sheet',
            '--page-size-mode', '2', '-o', str(svg_path), str(pcb_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    return p.returncode, (p.stderr or '')[-300:]


def main(argv=None):
    ap = argparse.ArgumentParser(description='add an Edge.Cuts rectangle around a KiCad board drawing')
    ap.add_argument('pcb')
    ap.add_argument('--out', default=None, help='write here instead of in place')
    ap.add_argument('--pad', type=float, default=1.0, help='mm added on every side')
    ap.add_argument('--export', default=None, help='also export this SVG')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    box = add_outline(args.pcb, args.out, pad=args.pad)
    if args.export:
        box['export_rc'] = export_svg(args.out or args.pcb, args.export)[0]
    print(json.dumps(box, indent=1) if args.json else
          'outline %s x %s mm around %d drawings -> %s' % (box['outline_mm'][0], box['outline_mm'][1],
                                                           box['drawings'], args.out or args.pcb))
    return 0 if box['outline_mm'] else 1


if __name__ == '__main__':
    sys.exit(main())
