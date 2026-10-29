#!/usr/bin/env python
"""The backlog's open questions, measured in one pass.

    python tools/construction_media_trial.py --figure <photo.png> --mannequin <blocks.png> --out <temp dir>

Three questions, all of them about combinations nobody has rendered yet:

  A. Does a genre style finally land its genre when the *prompt* carries the genre too (diagram + the construction words)?
  B. What does a drawing medium do to a *construction* prompt over a block figure - the artefact an artist asks for?
  C. Are the style strengths shipped on inference (pencil, watercolour, icon, blueprint) actually right, and does turbo's
     +0.06 hold for the other media?

Two questions per render: the medium, and the content. One question would let either hide the other.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scene_grade  # noqa: E402
import zimage_ui  # noqa: E402

KLEIN, TURBO = 'flux2 klein 9b uncensored', 'z-image turbo'
SUBJECT = 'a seated woman in a pale dress in a drawing room'
CONSTRUCTION = ('thin construction lines over the figure: centre line, a box around the ribcage, cylinders on the '
                'limbs, white background, black line only')
CONTENT = 'Is the picture a seated woman in a dress, in a chair, with her legs crossed?'


def round2():
    """The three candidates the first round left open.

    W  watercolour with an object anchor instead of three adjectives - it was the one style that did not land.
    G  a genre style *with* its genre words in the prompt (diagram worked that way; icon and blueprint were never tried).
    P  a construction study that asks for the source pose by name, since both winners came back frontal.
    """
    anchored = 'watercolour on rough cold-press paper, visible brush strokes, paper showing through'
    icon_words = 'flat vector icon of a seated woman: simple shapes, flat colour, plain white background'
    blueprint_words = 'engineering blueprint of a seated woman: white lines on a blue ground, orthographic'
    pose_blue = ('thin construction lines over the figure in the same seated pose: centre line, a box around the ribcage, '
                 'cylinders on the limbs, blue pencil underdrawing on white paper')
    watercolour_q = 'Is this a watercolour painting - visible brush strokes on textured paper - rather than a photograph?'
    icon_q = 'Is this a flat vector icon - simple shapes, flat colour, plain background - rather than a photograph?'
    blueprint_q = 'Is this an engineering blueprint - white lines on a blue ground - rather than a photograph?'
    blue_q = 'Is this drawn in blue pencil lines?'
    return [
        ('W1 watercolour anchored at 0.72, klein', SUBJECT + ', ' + anchored, 'figure', 0.72, KLEIN, watercolour_q,
         CONTENT),
        ('W2 watercolour anchored at 0.78, klein', SUBJECT + ', ' + anchored, 'figure', 0.78, KLEIN, watercolour_q,
         CONTENT),
        ('G1 icon + genre words at 0.78, klein', icon_words, 'figure', 0.78, KLEIN, icon_q, CONTENT),
        ('G2 blueprint + genre words at 0.78, klein', blueprint_words, 'figure', 0.78, KLEIN, blueprint_q, CONTENT),
        ('P1 blue pencil construction + named pose at 0.72, klein', pose_blue, 'mannequin', 0.72, KLEIN, blue_q,
         'Is the figure seated, with one arm resting down and the legs crossed to one side?'),
        ('P2 blue pencil construction + named pose at 0.65, klein', pose_blue, 'mannequin', 0.65, KLEIN, blue_q,
         'Is the figure seated, with one arm resting down and the legs crossed to one side?'),
    ]


def plan():
    """(label, prompt, base, strength, model, medium question, content question)"""
    diagram_q = ('Is this a technical line diagram of a figure - thin black lines on a plain white background - rather '
                 'than a photograph?')
    blocks_q = 'Does the figure carry drawn construction lines and a body built from simple blocks?'
    return [
        ('A1 diagram style + construction words, klein', CONSTRUCTION + ', ' + zimage_ui.STYLES['diagram'], 'figure',
         0.78, KLEIN, diagram_q, blocks_q),
        ('A2 diagram style + construction words, turbo', CONSTRUCTION + ', ' + zimage_ui.STYLES['diagram'], 'figure',
         0.78, TURBO, diagram_q, blocks_q),
        ('B1 charcoal + construction over the blocks, klein', CONSTRUCTION + ', ' + zimage_ui.STYLES['charcoal'],
         'mannequin', 0.72, KLEIN, 'Is this a charcoal drawing on textured paper?', blocks_q),
        ('B2 blue pencil + construction over the blocks, klein', CONSTRUCTION + ', ' + zimage_ui.STYLES['blue pencil'],
         'mannequin', 0.72, KLEIN, 'Is this drawn in blue pencil lines?', blocks_q),
        ('C1 pencil at 0.72, klein', SUBJECT + ', ' + zimage_ui.STYLES['pencil'], 'figure', 0.72, KLEIN,
         'Is this a graphite pencil sketch (hatching, paper) rather than a photograph?', CONTENT),
        ('C2 watercolour at 0.72, klein', SUBJECT + ', ' + zimage_ui.STYLES['watercolour'], 'figure', 0.72, KLEIN,
         'Is this a watercolour painting - soft washes on paper - rather than a photograph?', CONTENT),
        ('C3 icon at 0.78, klein', SUBJECT + ', ' + zimage_ui.STYLES['icon'], 'figure', 0.78, KLEIN,
         'Is this a flat vector icon - simple shapes on a plain background - rather than a photograph?', CONTENT),
        ('C4 blueprint at 0.78, klein', SUBJECT + ', ' + zimage_ui.STYLES['blueprint'], 'figure', 0.78, KLEIN,
         'Is this an engineering blueprint - white lines on a blue ground - rather than a photograph?', CONTENT),
        ('D1 pencil at 0.78, turbo', SUBJECT + ', ' + zimage_ui.STYLES['pencil'], 'figure', 0.78, TURBO,
         'Is this a graphite pencil sketch (hatching, paper) rather than a photograph?', CONTENT),
        ('D2 blue pencil at 0.78, turbo', SUBJECT + ', ' + zimage_ui.STYLES['blue pencil'], 'figure', 0.78, TURBO,
         'Is this drawn in blue pencil lines?', CONTENT),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--figure', required=True)
    ap.add_argument('--mannequin', required=True, help='the plain block figure, no construction guides on it')
    ap.add_argument('--out', default=r'C:/Users/kevin/AppData/Local/Temp/construction-media')
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--seed', type=int, default=4245)
    ap.add_argument('--round2', action='store_true', help='the three follow-up candidates instead of the first ten')
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    bases = {'figure': Path(a.figure), 'mannequin': Path(a.mannequin)}

    engine = zimage_ui.Engine(port=8006, progress=False)
    if not engine._answers():
        raise SystemExit('start the server first')

    rows = []
    for label, prompt, base, strength, model, q_medium, q_content in (round2() if a.round2 else plan()):
        steps = 4 if model == KLEIN else 9
        print('\n-- %s' % label, flush=True)
        while not engine.events.empty():
            try:
                engine.events.get_nowait()
            except Exception:
                break
        before = set(p.name for p in out.glob('*.png'))
        engine.generate(prompt, a.size, a.size, steps, 1.0, a.seed, str(out),
                        base_path=str(bases[base]), denoise=strength, profile=model)
        child, deadline = None, time.time() + 600
        while time.time() < deadline:
            try:
                kind, payload = engine.events.get(timeout=5)
            except Exception:
                continue
            if kind == 'image':
                child = Path(payload[1])
                break
            if kind in ('error', 'cancelled'):
                print('   %s: %s' % (kind, payload), flush=True)
                break
        if child is None:
            new = set(p.name for p in out.glob('*.png')) - before
            child = (out / sorted(new)[-1]) if new else None
        if child is None:
            rows.append((label, 'no image', '', '', ''))
            continue
        medium, m_reason = scene_grade.ask(child, q_medium, scene_grade.SCENE_SYSTEM)
        content, c_reason = scene_grade.ask(child, q_content, scene_grade.SCENE_SYSTEM)
        print('   %-42s medium %-4s content %-4s | %s' % (child.name[-38:], medium, content, m_reason[:44]), flush=True)
        rows.append((label, medium, content, m_reason, str(child)))

    print('\n| probe | medium | content | file |')
    print('|---|---|---|---|')
    for label, medium, content, _r, path in rows:
        print('| %s | **%s** | %s | %s |' % (label, medium, content, Path(path).name if path else '-'))
    print()
    for label, medium, content, _r, _p in rows:
        if medium == 'yes' and content == 'yes':
            print('- WORKS: %s' % label)


if __name__ == '__main__':
    main()
