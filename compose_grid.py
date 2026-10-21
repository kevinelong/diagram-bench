"""compose_grid.py - 2x2 grid of the four configurations, cropped to content and captioned."""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont

FONT = 'C:/Windows/Fonts/segoeui.ttf'
FONT_B = 'C:/Windows/Fonts/segoeuib.ttf'
CELL_W, PAD, CAP = 700, 26, 74


def crop(im, pad=8):
    bg = Image.new('RGB', im.size, (255, 255, 255))
    box = ImageChops.difference(im, bg).getbbox()
    if not box:
        return im
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad))
    return im.crop(box)


def load(p):
    im = crop(Image.open(p).convert('RGB'))
    return im.resize((CELL_W, max(1, int(im.height * CELL_W / im.width))), Image.LANCZOS)


panels = [
    ('hard/pooltable-methodb.png', '1 · bare spec', 'cue 0° — horizontal', (200, 30, 30)),
    ('hard/pooltable-b-example.png', '2 · + worked SVG example', 'cue 0° — example ignored', (200, 30, 30)),
    ('hard/pooltable-kit-rect.png', '3 · + kit with line_at()', 'cue −15.0° — rotated rect (transform)', (5, 130, 80)),
    ('hard/pooltable-kit-lines.png', '4 · + <rect> banned', 'cue +30.0° — line with computed angle', (5, 130, 80)),
]
imgs = [(load(p), t, s, c) for p, t, s, c in panels]
row_h = [max(imgs[0][0].height, imgs[1][0].height), max(imgs[2][0].height, imgs[3][0].height)]
canvas = Image.new('RGB', (CELL_W * 2 + PAD * 3, sum(row_h) + CAP * 2 + PAD * 3), 'white')
d = ImageDraw.Draw(canvas)
f_t, f_s = ImageFont.truetype(FONT_B, 21), ImageFont.truetype(FONT, 17)
y_cursor = PAD
for row in (0, 1):
    for col in (0, 1):
        idx = row * 2 + col
        im, title, sub, colour = imgs[idx]
        x = PAD + col * (CELL_W + PAD)
        canvas.paste(im, (x, y_cursor))
        d.text((x, y_cursor + im.height + 8), title, font=f_t, fill=(20, 20, 20))
        d.text((x, y_cursor + im.height + 34), sub, font=f_s, fill=colour)
    y_cursor += row_h[row] + CAP
canvas.save('hard/pooltable-progression.png')
print('wrote hard/pooltable-progression.png %dx%d  row heights %s' % (canvas.size + (row_h,)))
