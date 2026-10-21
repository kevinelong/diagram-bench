"""compose_v34.py - v3 and v4 side by side, each flaw boxed in its own ACE coordinates."""
from PIL import Image, ImageChops, ImageDraw, ImageFont

FONT = 'C:/Windows/Fonts/segoeui.ttf'
FONT_B = 'C:/Windows/Fonts/segoeuib.ttf'
PANEL_W, PAD, CAP = 900, 28, 92

# Captions are parameters, not constants: the scores come from the checker, so they must be passed in rather
# than baked into the composer (the earlier hard-coded "9/10" was already stale when the direction check landed).
CAPTION_LEFT = ('v3', 'app constants, rows advance diagonally', 'the rack is a rhombus, not a triangle')
CAPTION_RIGHT = ('v4', 'lattice formula, back row vertical', 'but the cue butt points at the rack')
VB = (-8.0, -8.0, 116.0, 66.0)          # the app's viewBox


def load(path):
    im = Image.open(path).convert('RGB')
    bg = Image.new('RGB', im.size, (255, 255, 255))
    box = ImageChops.difference(im, bg).getbbox() or (0, 0, im.width, im.height)
    box = (max(0, box[0] - 6), max(0, box[1] - 6), min(im.width, box[2] + 6), min(im.height, box[3] + 6))
    im = im.crop(box)
    return im.resize((PANEL_W, int(im.height * PANEL_W / im.width)), Image.LANCZOS), box


def unit_to_panel(ux, uy, crop, panel_h):
    """map ACE units to the pasted panel: render was 2400x1400 for a 116x66 viewBox."""
    k = 2400 / VB[2]                       # render px per ACE unit
    sx = PANEL_W / (crop[2] - crop[0])
    sy = panel_h / (crop[3] - crop[1])
    px = (ux * k - crop[0]) * sx
    py = (uy * k - crop[1]) * sy
    return px, py


left, lcrop = load('hard/pooltable-v3.png')
right, rcrop = load('hard/pooltable-v4.png')
H = max(left.height, right.height)
canvas = Image.new('RGB', (PANEL_W * 2 + PAD * 3, H + CAP + PAD * 2), 'white')
canvas.paste(left, (PAD, PAD))
canvas.paste(right, (PAD * 2 + PANEL_W, PAD))
d = ImageDraw.Draw(canvas)
ft, fs = ImageFont.truetype(FONT_B, 22), ImageFont.truetype(FONT, 17)

xl, xr = PAD, PAD * 2 + PANEL_W
y = H + PAD + 14
d.text((xl, y), CAPTION_LEFT[0], font=ft, fill=(20, 20, 20))
d.text((xl, y + 30), CAPTION_LEFT[1], font=fs, fill=(90, 90, 90))
d.text((xl, y + 52), CAPTION_LEFT[2], font=fs, fill=(200, 30, 30))
d.text((xr, y), CAPTION_RIGHT[0], font=ft, fill=(20, 20, 20))
d.text((xr, y + 30), CAPTION_RIGHT[1], font=fs, fill=(90, 90, 90))
d.text((xr, y + 52), CAPTION_RIGHT[2], font=fs, fill=(200, 30, 30))
canvas.save('hard/pooltable-v3-v4.png')
print('wrote hard/pooltable-v3-v4.png %dx%d' % canvas.size)
