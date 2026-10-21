"""compose_compare.py - put two arm renders side by side, cropped to content and captioned."""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont

FONT = 'C:/Windows/Fonts/segoeui.ttf'
FONT_B = 'C:/Windows/Fonts/segoeuib.ttf'
W_TARGET = 880
PAD = 30
CAP_H = 96


def crop_to_content(img, pad=8):
    bg = Image.new('RGB', img.size, (255, 255, 255))
    box = ImageChops.difference(img.convert('RGB'), bg).getbbox()
    if not box:
        return img
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    return img.crop(box)


def load(path):
    raw = Image.open(path).convert('RGB')
    bg = Image.new('RGB', raw.size, (255, 255, 255))
    box = ImageChops.difference(raw, bg).getbbox() or (0, 0, raw.width, raw.height)
    box = (max(0, box[0] - 8), max(0, box[1] - 8), min(raw.width, box[2] + 8), min(raw.height, box[3] + 8))
    im = raw.crop(box)
    scale = W_TARGET / im.width
    return im.resize((W_TARGET, max(1, int(im.height * scale))), Image.LANCZOS), box, scale


def cue_bbox_svg(path):
    """bbox of the thick horizontal element - the cue - measured from the SVG, not guessed."""
    import xml.etree.ElementTree as ET
    ns = '{http://www.w3.org/2000/svg}'
    root = ET.fromstring(Path(path).read_text(encoding='utf-8'))
    best = None
    for el in root.iter():
        tag = el.tag.replace(ns, '')
        f = lambda a: float(str(el.get(a, 0)).replace('px', ''))
        if tag == 'rect':
            w, h = f('width'), f('height')
            if w >= 100 and 4 <= h <= 20:
                cand = (f('x'), f('y'), f('x') + w, f('y') + h)
            else:
                continue
        elif tag == 'line':
            x1, y1, x2, y2 = f('x1'), f('y1'), f('x2'), f('y2')
            if abs(y1 - y2) <= 8 and abs(x2 - x1) >= 100 and f('stroke-width') >= 4:
                cand = (min(x1, x2), min(y1, y2) - 8, max(x1, x2), max(y1, y2) + 8)
            else:
                continue
        else:
            continue
        if best is None or (cand[2] - cand[0]) > (best[2] - best[0]):
            best = cand
    return best


left, lbox, lscale = load('hard/pooltable-methodb.png')
right, rbox, rscale = load('hard/pooltable-methode-final.png')
H = max(left.height, right.height)
canvas = Image.new('RGB', (W_TARGET * 2 + PAD * 3, H + CAP_H + PAD * 2), 'white')
canvas.paste(left, (PAD, PAD))
canvas.paste(right, (PAD * 2 + W_TARGET, PAD))

d = ImageDraw.Draw(canvas)
f_title = ImageFont.truetype(FONT_B, 22)
f_body = ImageFont.truetype(FONT, 17)

x_left = PAD
x_right = PAD * 2 + W_TARGET
y = H + PAD + 14
d.text((x_left, y), 'method B · code → SVG · 6/7', font=f_title, fill=(20, 20, 20))
d.text((x_left, y + 30), 'all 22 balls inside the table, aspect 2.0 —', font=f_body, fill=(90, 90, 90))
d.text((x_left, y + 52), 'but the cue stick is horizontal', font=f_body, fill=(200, 30, 30))
cue = cue_bbox_svg('hard/pooltable-qwen3-coder_30b.svg')
if cue:
    cx0 = PAD + (cue[0] - lbox[0]) * lscale
    cy0 = PAD + (cue[1] - lbox[1]) * lscale
    cx1 = PAD + (cue[2] - lbox[0]) * lscale
    cy1 = PAD + (cue[3] - lbox[1]) * lscale
    d.rectangle([cx0 - 10, cy0 - 18, cx1 + 10, cy1 + 18], outline=(200, 30, 30), width=3)

d.text((x_right, y), 'method E · code → KiCad · 6/7', font=f_title, fill=(20, 20, 20))
d.text((x_right, y + 30), 'cue angled at 30°, aspect 2.0 —', font=f_body, fill=(90, 90, 90))
d.text((x_right, y + 52), 'but the 15-ball rack is drawn beyond the right rail', font=f_body, fill=(200, 30, 30))
# rack: outside centres measured from the board (mm); the panel shows the whole export viewBox, so map directly
OUTS = [(86.5, 24.0), (89.5, 22.5), (89.5, 28.5), (92.5, 26.0), (86.5, 27.0), (86.5, 30.0)]
vb_w = 93.345
k = W_TARGET / vb_w
xs = [x_right + mx * k for mx, _ in OUTS]
ys = [PAD + my * k for _, my in OUTS]
cox0, coy0, cox1, coy1 = min(xs), min(ys), max(xs), max(ys)
d.rectangle([cox0 - 22, coy0 - 22, cox1 + 22, coy1 + 22], outline=(200, 30, 30), width=3)

d.line([(W_TARGET + PAD + PAD // 2, 0), (W_TARGET + PAD + PAD // 2, canvas.height)], fill=(220, 220, 220), width=2)

canvas.save('hard/pooltable-compare.png')
print('wrote hard/pooltable-compare.png  %dx%d' % canvas.size)
