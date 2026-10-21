"""ladder.py - two separable experiments on why coordinate prompts cost text.

Experiment 1 (density): the SAME bar chart, described with increasing amounts of its own geometry stated as
numbers - heights only, then positions, then width and axis, then label positions.  The target never changes, so
any change in legibility is attributable to the number count rather than to a different drawing.

Experiment 2 (identity binding): the SAME tree, with the seven labels bound to their own nodes ("node 3 is the
circle at 212,620 containing the number 3") instead of a phrase repeated seven times.

    python ladder.py            # writes prompts-ladder.json and prints the numeric token count per variant
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUFFIX = json.loads((ROOT / 'prompts-diagrams.json').read_text(encoding='utf-8'))['params']['style_suffix']

CANVAS = "a 1024 by 1024 canvas, white background, flat 2D vector style"

# One target, four levels of numeric specification.  Task chosen deliberately: bar-1 reads 1/4 in every arm
# measured so far (frozen, v1, v2), so it cannot show a response.  network-1 reads 1/4, 1/4, 3/4 - it responds.
NET = "A network topology diagram on %s." % CANVAS
LINKS = "one central rectangle labelled SERVER connected by straight lines to three smaller rectangles " \
        "labelled PC1, PC2, PC3, black outlines, white background"
NETWORK = {
    "net-1-words": NET + " With " + LINKS + ".",
    "net-2-server": NET + " With " + LINKS + ". The server rectangle is 240 by 110 at 392,140.",
    "net-3-pcs": NET + " With " + LINKS + ". The server rectangle is 240 by 110 at 392,140; the three "
                  "workstation rectangles are 200 by 100 at 112,700, at 412,700 and at 712,700.",
    "net-4-everything": NET + " With " + LINKS + ". The server rectangle is 240 by 110 at 392,140; the three "
                        "workstation rectangles are 200 by 100 at 112,700, at 412,700 and at 712,700. Each link "
                        "starts at 512,250 and ends at the top edge of its workstation: 212,700, 512,700 and "
                        "812,700. The labels sit inside their boxes, centred at 512,204, 212,762, 512,762 and "
                        "812,762.",
}

NODES = [(512, 150), (312, 360), (712, 360), (212, 620), (412, 620), (612, 620), (812, 620)]
EDGES = [(1, 2), (1, 3), (2, 4), (2, 5), (3, 6), (3, 7)]


def tree_ident():
    parts = ["A binary tree diagram on %s." % CANVAS]
    for i, (x, y) in enumerate(NODES, start=1):
        parts.append("Node %d is a circle centre %d,%d radius 70 containing the number %d." % (i, x, y, i))
    parts.append("Connect them with straight lines: " + ", ".join("node %d to node %d" % e for e in EDGES) + ".")
    parts.append("Every number is a 60 px Arial digit centred inside its own circle.")
    return " ".join(parts)


def tree_boundary():
    # boundary + spacing + count, the shape the rack used: no per-node coordinate schema.
    return ("A binary tree diagram on %s: seven circles of radius 70. Four sit in the bottom row centred at "
            "y = 620 and evenly spaced in x; two sit above them at y = 360, positioned above the gaps; one sits "
            "at the top centre at 512,150. Each circle contains its own number, reading left to right: bottom row "
            "4, 5, 6, 7; middle row 2, 3; top 1. Draw a straight line from every circle down to each of its "
            "children." % CANVAS)


PROMPTS = dict(NETWORK)
PROMPTS["tree-1-boundary"] = tree_boundary()
PROMPTS["tree-1-ident"] = tree_ident()
LABELS = {k: ['SERVER', 'PC1', 'PC2', 'PC3'] for k in NETWORK}
LABELS["tree-1-ident"] = [str(i) for i in range(1, 8)]
KIND = {k: 'topology' for k in NETWORK}
KIND["tree-1-ident"] = 'hierarchy'
KIND["tree-1-boundary"] = 'hierarchy'
LABELS["tree-1-boundary"] = [str(i) for i in range(1, 8)]


def numbers_in(text):
    # every numeric token in the prompt, which is what competes for the model's attention
    return re.findall(r'-?\d+(?:\.\d+)?', text)


def main():
    out = {"declared": "2026-10-21", "kind": "experiment corpus (not part of the frozen corpus)",
           "note": ("Density ladder on network-1 - one unchanged target, four increasing levels of numeric "
                    "specification - plus an identity-binding variant of the tree. bar-1 was rejected as the "
                    "ladder task: it reads 1/4 in every arm measured, so it has no response to show."),
           "prompts": [{"id": k, "kind": KIND[k], "labels": LABELS[k], "prompt": v} for k, v in PROMPTS.items()]}
    (ROOT / 'prompts-ladder.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print('%-14s numbers  prompt chars' % 'variant')
    for k, v in PROMPTS.items():
        print('  %-12s %5d  %8d' % (k, len(numbers_in(v)), len(v)))
    print('\nwrote prompts-ladder.json (%d variants)' % len(PROMPTS))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
