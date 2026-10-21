"""make_synthetic.py - a diagram with known ground truth, to validate trace_score.py.

Draws the flow-1 prompt by hand: four rectangles, three vertical connectors, four labels.
Ground truth: 4 boxes, 3 connectors, 4 text strings, everything axis-aligned, 2 ink colours.
If the scorer does not recover approximately that, the scorer is wrong and no model number from
it can be trusted.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W = H = 1024
img = Image.new('RGB', (W, H), 'white')
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 44)
except Exception:
    font = ImageFont.load_default()

labels = ['START', 'VALIDATE', 'STORE', 'DONE']
box_w, box_h, gap = 300, 120, 110
x0 = (W - box_w) // 2
boxes = []
for i, label in enumerate(labels):
    y0 = 60 + i * (box_h + gap)
    boxes.append((x0, y0, x0 + box_w, y0 + box_h))
    d.rectangle(boxes[i], outline='black', width=6)
    tw = d.textlength(label, font=font)
    d.text((x0 + (box_w - tw) / 2, y0 + box_h / 2 - 26), label, fill='black', font=font)
for a, b in zip(boxes, boxes[1:]):
    x = x0 + box_w // 2
    d.line([(x, a[3]), (x, b[1])], fill='black', width=6)

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'synthetic-flow.png')
img.save(out)
print('wrote', out, 'boxes=4 connectors=3 labels=4 axis-aligned=yes colours=2')
