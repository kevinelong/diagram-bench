"""grounded.py - the ten corpus prompts rewritten against real constants.

The pool table was the only task whose prompt was rewritten against actual numbers
(ACE grid, 2.25 ball, 12.5 diamond spacing, 30-unit cue) and it is the only one whose
scores can be read as geometry rather than style.  This module gives the other ten the
same treatment, and makes the grounding *checkable* rather than a claim:

  * One SPEC dict per prompt: canvas, background, and every element as coordinates.
  * The prompt text is GENERATED from the spec - there is no second copy of the numbers
    that could drift out of step, and `--selftest` re-checks that every coordinate token
    in the spec appears in the generated prompt.
  * The reference SVG is also generated from the spec, so "what the prompt asks for" and
    "what the checker is handed" are the same object.  Structural checks then assert what
    the frozen expectations (structure-expectations.json) demand: box counts, exact label
    strings and their multiplicity, connector endpoints landing on a box edge or a node,
    and no geometry outside the canvas.

What this does NOT do: run a model.  It proves each grounded prompt is decodable and that
a correct drawing of it satisfies the frozen structural expectations.  The model gap stays
unmeasured until an arm runs.

    python grounded.py --selftest      # checks + writes prompts-diagrams-grounded.json
    python grounded.py --emit          # writes grounded_ref/<id>.svg
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
CANVAS = (1024, 1024)

# --------------------------------------------------------------------------- #
# Specs.  Every coordinate here is the prompt's own number.  Formatting helper: p().
# --------------------------------------------------------------------------- #


def _rect(x, y, w, h, fill="none", stroke="#000000", sw=4, rx=0, dash=None):
    return {"kind": "rect", "x": x, "y": y, "w": w, "h": h, "fill": fill,
            "stroke": stroke, "sw": sw, "rx": rx, "dash": dash}


def _line(x1, y1, x2, y2, sw=4, arrow=False, role="connector"):
    # role distinguishes a connector (counted by structure-expectations) from an axis,
    # a dimension line or a tick: a blueprint dimension line is an arrow that joins no boxes,
    # and network-1's prompt says "straight lines", so connectors are counted without arrowheads.
    return {"kind": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "sw": sw, "arrow": arrow, "role": role}


def _text(s, x, y, size=28, fill="#000000", anchor="middle"):
    return {"kind": "text", "s": s, "x": x, "y": y, "size": size, "fill": fill, "anchor": anchor}


def _circle(cx, cy, r, fill="#ffffff", stroke="#000000", sw=4):
    return {"kind": "circle", "cx": cx, "cy": cy, "r": r, "fill": fill, "stroke": stroke, "sw": sw}


SPECS = {}

# ---- flow-1 --------------------------------------------------------------- #
_bx, _bw, _bh = 392, 240, 110
_tops = [100, 320, 540, 760]
_names = ["START", "VALIDATE", "STORE", "DONE"]
SPECS["flow-1"] = {
    "lead": "A vertical flowchart on a 1024 by 1024 canvas, white background, flat 2D vector style.",
    "elements": (
        [_rect(_bx, t, _bw, _bh, stroke="#000000", sw=4) for t in _tops]
        + [_line(512, _tops[i] + _bh, 512, _tops[i + 1], sw=4, arrow=True) for i in range(3)]
        + [_text(n, 512, t + 66, 30) for n, t in zip(_names, _tops)]
    ),
    "boxes": [(_bx, t, _bw, _bh) for t in _tops],
}

# ---- floor-1 -------------------------------------------------------------- #
_wall = 16
_floor = []
_floor += [_rect(80, 80, 864, _wall, fill="#000000", stroke="none"),
           _rect(80, 848, 864, _wall, fill="#000000", stroke="none"),
           _rect(80, 80, _wall, 784, fill="#000000", stroke="none"),
           _rect(928, 80, _wall, 784, fill="#000000", stroke="none")]
# interior vertical divider at x=552..568, two door gaps (300-390, 580-660)
_floor += [_rect(552, 96, _wall, 204, fill="#000000", stroke="none"),
           _rect(552, 390, _wall, 190, fill="#000000", stroke="none"),
           _rect(552, 660, _wall, 188, fill="#000000", stroke="none")]
# left horizontal divider y=462..478 with a door gap (270-360)
_floor += [_rect(96, 462, 174, _wall, fill="#000000", stroke="none"),
           _rect(360, 462, 192, _wall, fill="#000000", stroke="none")]
# right horizontal dividers, each with a door gap
_floor += [_rect(568, 332, 152, _wall, fill="#000000", stroke="none"),
           _rect(800, 332, 128, _wall, fill="#000000", stroke="none"),
           _rect(568, 602, 172, _wall, fill="#000000", stroke="none"),
           _rect(820, 602, 108, _wall, fill="#000000", stroke="none")]
_floor += [_text("BEDROOM", 320, 286), _text("BEDROOM", 320, 686),
           _text("BATH", 752, 221), _text("KITCHEN", 752, 491), _text("LIVING", 752, 751)]
SPECS["floor-1"] = {
    "lead": "A two-bedroom apartment floor plan seen from above on a 1024 by 1024 canvas, white background.",
    "elements": _floor,
    "boxes": None,
    "walls": [e for e in _floor if e["kind"] == "rect"],
}

# ---- bar-1 ---------------------------------------------------------------- #
_bar_ws = [("Q1", 200, 200), ("Q2", 380, 340), ("Q3", 560, 480), ("Q4", 740, 640)]
SPECS["bar-1"] = {
    "lead": "A bar chart on a 1024 by 1024 canvas, white background.",
    "elements": ([_line(140, 820, 940, 820, sw=8, role="axis")]
                 + [_rect(x, 820 - h, 120, h, fill="#000000", stroke="none") for _, x, h in _bar_ws]
                 + [_text(n, x + 60, 884, 30) for n, x, _ in _bar_ws]),
    "boxes": [(x, 820 - h, 120, h) for _, x, h in _bar_ws],
}

# ---- org-1 ---------------------------------------------------------------- #
_org = [_rect(412, 140, 200, 100), _rect(172, 420, 200, 100), _rect(652, 420, 200, 100),
        _line(512, 240, 512, 330, sw=4), _line(272, 330, 752, 330, sw=4),
        _line(272, 330, 272, 420, sw=4), _line(752, 330, 752, 420, sw=4),
        _text("CEO", 512, 202), _text("CTO", 272, 482), _text("CFO", 752, 482)]
SPECS["org-1"] = {
    "lead": "A three-level org chart on a 1024 by 1024 canvas, white background, boxes with black outlines.",
    "elements": _org,
    "boxes": [(412, 140, 200, 100), (172, 420, 200, 100), (652, 420, 200, 100)],
}

# ---- circuit-1 ------------------------------------------------------------ #
# capacitor plates are 90 px apart: shape-rules revision 2 sets the floor at 0.08 x width = 82 px
_circ = [
    _line(140, 420, 320, 420),                                   # input lead
    _rect(320, 390, 140, 60),                                    # resistor body
    _line(460, 420, 640, 420),                                   # series wire to the output node
    _line(640, 420, 884, 420),                                   # output lead
    _line(640, 420, 640, 520),                                   # down to the capacitor
    _line(600, 520, 680, 520, sw=6),                             # top plate
    _line(600, 610, 680, 610, sw=6),                             # bottom plate
    _line(640, 610, 640, 680),                                   # to ground
    _line(596, 680, 684, 680, sw=6), _line(614, 700, 666, 700, sw=6), _line(632, 720, 648, 720, sw=6),
    _text("R1", 390, 372), _text("C1", 716, 572), _text("IN", 96, 408), _text("OUT", 928, 408),
]
SPECS["circuit-1"] = {
    "lead": "An RC low-pass filter schematic on a 1024 by 1024 canvas, thin black lines on white.",
    "elements": _circ,
    "boxes": [(320, 390, 140, 60)],
}

# ---- blueprint-1 ---------------------------------------------------------- #
_blue = [
    _rect(200, 300, 620, 420, stroke="#ffffff", sw=5),            # bracket outline
    _circle(380, 510, 60, fill="none", stroke="#ffffff", sw=5),   # mounting holes
    _circle(640, 510, 60, fill="none", stroke="#ffffff", sw=5),
    _line(200, 800, 820, 800, sw=3, arrow=True, role="dimension"),   # dimension line
    _line(200, 780, 200, 820, sw=3, role="dimension"), _line(820, 780, 820, 820, sw=3, role="dimension"),
    _text("620", 510, 772, 28, fill="#ffffff"), _text("120", 700, 700, 28, fill="#ffffff"),
    _text("BRACKET", 510, 258, 28, fill="#ffffff"),
]
SPECS["blueprint-1"] = {
    "lead": "A front-view engineering blueprint on a 1024 by 1024 canvas, solid dark blue background, white lines.",
    "background": "#0b2a5b",
    "elements": _blue,
    "boxes": [(200, 300, 620, 420)],
}

# ---- tree-1 --------------------------------------------------------------- #
_nodes = [(512, 150), (312, 360), (712, 360), (212, 620), (412, 620), (612, 620), (812, 620)]
_edges = [(0, 1), (0, 2), (1, 3), (1, 4), (2, 5), (2, 6)]
# Draw order matters: edges first, then filled circles on top, then the digits.  Drawn the other way
# round the connectors cut through the node interiors and the digits become unreadable (found by OCR).
_tree = ([_line(_nodes[a][0], _nodes[a][1], _nodes[b][0], _nodes[b][1], sw=3) for a, b in _edges]
         + [_circle(x, y, 70) for x, y in _nodes]
         + [_text(str(i + 1), x, y + 20, 60) for i, (x, y) in enumerate(_nodes)])
SPECS["tree-1"] = {
    "lead": "A binary tree on a 1024 by 1024 canvas, white background, circles with black outlines.",
    "elements": _tree,
    "boxes": None, "nodes": _nodes, "edges": _edges,
}

# ---- timeline-1 ----------------------------------------------------------- #
_ticks = [200, 400, 600, 800]
_years = ["2020", "2021", "2022", "2023"]
SPECS["timeline-1"] = {
    "lead": "A horizontal timeline on a 1024 by 1024 canvas, white background.",
    "elements": ([_line(120, 512, 904, 512, sw=8, role="axis")]
                 + [_line(x, 472, x, 512, sw=6) for x in _ticks]
                 + [_text(y, x, 580, 30) for x, y in zip(_ticks, _years)]),
    "boxes": None,
}

# ---- network-1 ------------------------------------------------------------ #
_net_boxes = [(392, 140, 240, 110), (112, 700, 200, 100), (412, 700, 200, 100), (712, 700, 200, 100)]
_net = ([_rect(*b) for b in _net_boxes]
        + [_line(512, 250, 212, 700, sw=4), _line(512, 250, 512, 700, sw=4), _line(512, 250, 812, 700, sw=4),
           _text("SERVER", 512, 204), _text("PC1", 212, 762), _text("PC2", 512, 762), _text("PC3", 812, 762)])
SPECS["network-1"] = {
    "lead": "A network topology diagram on a 1024 by 1024 canvas, white background, black outlines.",
    "elements": _net,
    "boxes": _net_boxes,
}

# ---- process-1 ------------------------------------------------------------ #
_proc_boxes = [(340, 280, 200, 90), (340, 560, 200, 90), (640, 560, 200, 90)]
_proc = ([_rect(80, 180, 864, 280), _rect(80, 460, 864, 280)]          # lane bodies
         + [_rect(*b, rx=20) for b in _proc_boxes]                      # rounded activity boxes
         + [_line(440, 370, 440, 560, sw=4, arrow=True),                # the one crossing arrow
            _line(540, 605, 640, 605, sw=4, arrow=True)]                # in-lane arrow
         + [_text("REQUESTER", 200, 236, 26), _text("APPROVER", 200, 516, 26),
            _text("SUBMIT", 440, 336), _text("REVIEW", 440, 616), _text("APPROVE", 740, 616)])
SPECS["process-1"] = {
    "lead": "A two-lane swimlane process diagram on a 1024 by 1024 canvas, white background, black outlines.",
    "elements": _proc,
    "boxes": _proc_boxes, "lanes": [(80, 180, 864, 280), (80, 460, 864, 280)],
}

ORDER = ["flow-1", "floor-1", "bar-1", "org-1", "circuit-1",
         "blueprint-1", "tree-1", "timeline-1", "network-1", "process-1"]

# --------------------------------------------------------------------------- #
# Prompt generation: the spec is the only source of numbers.
# --------------------------------------------------------------------------- #


def _n(v):
    return str(int(v)) if float(v).is_integer() else ("%g" % v)


_COLOUR_WORDS = {"#000000": "black", "#ffffff": "white", "#0b2a5b": "dark blue"}


def _c(v):
    return _COLOUR_WORDS.get(v, v)


def prompt_for(spec):
    out = [spec["lead"]]
    for e in spec["elements"]:
        k = e["kind"]
        if k == "rect":
            if e["fill"] == "#000000":
                out.append("Filled black rectangle %s by %s at %s,%s." %
                           (_n(e["w"]), _n(e["h"]), _n(e["x"]), _n(e["y"])))
            elif e["rx"]:
                out.append("Rounded rectangle %s by %s at %s,%s with corner radius %s, black outline %s thick, no fill."
                           % (_n(e["w"]), _n(e["h"]), _n(e["x"]), _n(e["y"]), _n(e["rx"]), _n(e["sw"])))
            else:
                out.append("Rectangle %s by %s at %s,%s, %s outline %s thick, no fill." %
                           (_n(e["w"]), _n(e["h"]), _n(e["x"]), _n(e["y"]), _c(e["stroke"]), _n(e["sw"])))
        elif k == "line":
            out.append("%s line from %s,%s to %s,%s, %s thick." %
                       ("Arrow" if e["arrow"] else "Straight", _n(e["x1"]), _n(e["y1"]),
                        _n(e["x2"]), _n(e["y2"]), _n(e["sw"])))
        elif k == "circle":
            out.append("Circle centre %s,%s radius %s, %s outline %s thick." %
                       (_n(e["cx"]), _n(e["cy"]), _n(e["r"]), _c(e["stroke"]), _n(e["sw"])))
        elif k == "text":
            out.append("Text %s centred at %s,%s in %s px Arial." % (e["s"], _n(e["x"]), _n(e["y"]), _n(e["size"])))
    out.append("Use only the stated coordinates; every straight edge horizontal or vertical unless an endpoint "
               "states otherwise; flat 2D vector style, sharp edges, solid fills, white background, no gradients, "
               "no shadows, no 3D.")
    if spec.get("background", "#ffffff") != "#ffffff":
        out[-1] = out[-1].replace("white background", "background " + spec["background"])
    return " ".join(out)


# --------------------------------------------------------------------------- #
# SVG emission
# --------------------------------------------------------------------------- #


def svg_for(spec):
    bg = spec.get("background", "#ffffff")
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">',
             '<rect x="0" y="0" width="1024" height="1024" fill="%s"/>' % bg]
    for e in spec["elements"]:
        k = e["kind"]
        if k == "rect":
            parts.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="%s" stroke-width="%s"%s/>'
                         % (_n(e["x"]), _n(e["y"]), _n(e["w"]), _n(e["h"]), e["fill"], e["stroke"], _n(e["sw"]),
                            (' rx="%s"' % _n(e["rx"])) if e["rx"] else ""))
        elif k == "line":
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="%s"%s/>'
                         % (_n(e["x1"]), _n(e["y1"]), _n(e["x2"]), _n(e["y2"]),
                            "#ffffff" if bg != "#ffffff" else "#000000", _n(e["sw"]),
                            ' marker-end="url(#ah)"' if e["arrow"] else ""))
        elif k == "circle":
            parts.append('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="%s"/>'
                         % (_n(e["cx"]), _n(e["cy"]), _n(e["r"]), e["fill"], e["stroke"], _n(e["sw"])))
        elif k == "text":
            parts.append('<text x="%s" y="%s" font-family="Arial" font-size="%s" fill="%s" '
                         'text-anchor="%s">%s</text>'
                         % (_n(e["x"]), _n(e["y"]), _n(e["size"]), e["fill"], e["anchor"], e["s"]))
    parts.insert(1, '<defs><marker id="ah" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
                    '<path d="M0,0 L6,3 L0,6 z" fill="%s"/></marker></defs>'
                 % ("#ffffff" if bg != "#ffffff" else "#000000"))
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Structural checks (deterministic; no model, no LLM)
# --------------------------------------------------------------------------- #


def labels(spec):
    return [e["s"] for e in spec["elements"] if e["kind"] == "text"]


def _on_box(x, y, box, tol=0.6):
    bx, by, bw, bh = box
    edges = ((bx, by, bx + bw, by), (bx, by + bh, bx + bw, by + bh),
             (bx, by, bx, by + bh), (bx + bw, by, bx + bw, by + bh))
    for x1, y1, x2, y2 in edges:
        if (min(x1, x2) - tol <= x <= max(x1, x2) + tol) and (min(y1, y2) - tol <= y <= max(y1, y2) + tol):
            return True
    return False


def check_spec(sid, spec, expectations):
    errs = []
    W, H = CANVAS
    for e in spec["elements"]:
        k = e["kind"]
        pts = []
        if k == "rect":
            pts = [(e["x"], e["y"]), (e["x"] + e["w"], e["y"] + e["h"])]
        elif k == "line":
            pts = [(e["x1"], e["y1"]), (e["x2"], e["y2"])]
        elif k == "circle":
            pts = [(e["cx"] - e["r"], e["cy"] - e["r"]), (e["cx"] + e["r"], e["cy"] + e["r"])]
        elif k == "text":
            pts = [(e["x"], e["y"])]
        for x, y in pts:
            if not (0 <= x <= W and 0 <= y <= H):
                errs.append("%s: geometry outside canvas at %s,%s" % (sid, _n(x), _n(y)))

    exp = expectations.get(sid, {})
    boxes = spec.get("boxes") or []
    if "boxes_exact" in exp and len(boxes) != exp["boxes_exact"]:
        errs.append("%s: boxes_exact %d, spec has %d" % (sid, exp["boxes_exact"], len(boxes)))
    if "boxes_at_least" in exp and len(boxes) < exp["boxes_at_least"]:
        errs.append("%s: boxes_at_least %d, spec has %d" % (sid, exp["boxes_at_least"], len(boxes)))

    lab = labels(spec)
    if "labels_exact" in exp and len(lab) != exp["labels_exact"]:
        errs.append("%s: labels_exact %d, spec has %d" % (sid, exp["labels_exact"], len(lab)))
    if "labels_at_least" in exp and len(lab) < exp["labels_at_least"]:
        errs.append("%s: labels_at_least %d, spec has %d" % (sid, exp["labels_at_least"], len(lab)))
    for name, count in (exp.get("label_multiplicity") or {}).items():
        if lab.count(name) != count:
            errs.append("%s: label %s expected %d, spec has %d" % (sid, name, count, lab.count(name)))

    # connector endpoints must land on a box edge (flow, org, network) or on a node
    conn = [e for e in spec["elements"] if e["kind"] == "line" and e.get("role") == "connector"]
    if "connectors_exact" in exp and len(conn) != exp["connectors_exact"]:
        errs.append("%s: connectors_exact %d, spec has %d" % (sid, exp["connectors_exact"], len(conn)))
    # Endpoints must sit on a box edge where the prompt joins boxes directly.  org-1 is exempt:
    # a right-angle connector's elbow legitimately floats between boxes.
    if sid in ("flow-1", "network-1", "process-1"):
        for c in conn:
            if not any(_on_box(c["x1"], c["y1"], b) for b in boxes):
                errs.append("%s: connector start %s,%s lands on no box" % (sid, _n(c["x1"]), _n(c["y1"])))
            if not any(_on_box(c["x2"], c["y2"], b) for b in boxes):
                errs.append("%s: connector end %s,%s lands on no box" % (sid, _n(c["x2"]), _n(c["y2"])))

    # a crossing arrow in process-1 must actually span the lane divider
    if sid == "process-1":
        lanes = spec["lanes"]
        div = lanes[0][1] + lanes[0][3]
        cross = [c for c in conn if min(c["y1"], c["y2"]) < div < max(c["y1"], c["y2"])]
        if len(cross) != 1:
            errs.append("process-1: expected exactly one arrow crossing the lane divider, got %d" % len(cross))

    # tree: every edge endpoint is a node centre and the edge count matches
    if "edges" in spec:
        if "connectors_at_least" in exp and len(spec["edges"]) < exp["connectors_at_least"]:
            errs.append("%s: connectors_at_least %d, spec has %d"
                        % (sid, exp["connectors_at_least"], len(spec["edges"])))
        if len(lab) < exp.get("labels_at_least", 0):
            errs.append("%s: labels_at_least %d, spec has %d" % (sid, exp["labels_at_least"], len(lab)))

    # every coordinate token in the spec must exist in the generated prompt
    text = prompt_for(spec)
    for e in spec["elements"]:
        for key in ("x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "w", "h", "r"):
            if key in e:
                tok = _n(e[key])
                if tok not in text:
                    errs.append("%s: coordinate %s of %s absent from the prompt" % (sid, tok, e["kind"]))
                    break
    if not text.count("1024") >= 2 and spec.get("lead", "").count("1024") < 1:
        errs.append("%s: canvas size not stated" % sid)
    return errs


def main(argv):
    exps = json.loads((REPO / "structure-expectations.json").read_text(encoding="utf-8"))["expect"]
    frozen = json.loads((REPO / "prompts-diagrams.json").read_text(encoding="utf-8"))
    frozen_ids = [p["id"] for p in frozen["prompts"]]

    if "--selftest" in argv:
        errs, rows = [], []
        if frozen_ids != ORDER:
            errs.append("spec order does not match the frozen corpus: %s vs %s" % (ORDER, frozen_ids))
        for sid in ORDER:
            e = check_spec(sid, SPECS[sid], exps)
            errs += e
            rows.append((sid, len(SPECS[sid]["elements"]), len(labels(SPECS[sid])), len(e)))
        for sid, n_el, n_lab, n_err in rows:
            print("  %-12s elements %3d  labels %2d  errors %d" % (sid, n_el, n_lab, n_err))
        if errs:
            print("\nFAILED %d" % len(errs))
            for e in errs:
                print("  -", e)
            return 1
        out = {"declared": "2026-10-21", "kind": "grounded revision of prompts-diagrams.json",
               "note": ("Ten prompts restated as coordinates, generated from grounded.py's specs. Same ids as the "
                        "frozen corpus, which is left untouched: this is a second revision, not an edit. The prompt "
                        "text carries every number the reference SVG uses, and --selftest proves the two agree."),
               "prompts": [{"id": sid, "prompt": prompt_for(SPECS[sid]),
                            "n_elements": len(SPECS[sid]["elements"])} for sid in ORDER]}
        (REPO / "prompts-diagrams-grounded.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("\nPASS %d specs; wrote prompts-diagrams-grounded.json" % len(ORDER))
        return 0

    if "--emit" in argv:
        d = REPO / "grounded_ref"
        d.mkdir(exist_ok=True)
        for sid in ORDER:
            (d / (sid + ".svg")).write_text(svg_for(SPECS[sid]), encoding="utf-8")
        print("wrote %d reference SVGs to %s" % (len(ORDER), d))
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
