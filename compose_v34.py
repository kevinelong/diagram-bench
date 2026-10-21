"""compose_v34.py - v3 and v4 side by side, each flaw boxed in its own ACE coordinates."""
from PIL import Image, ImageChops, ImageDraw, ImageFont

FONT = 'C:/Windows/Fonts/segoeui.ttf'
FONT_B = 'C:/Windows/Fonts/segoeuib.ttf'
PANEL_W, PAD, CAP = 900, 28, 92
VB = (-8.0, -8.0, 116.0, 66.0)          # the app's viewBox


def load(path):
    im = Image.open(path).convert('RGB')
    bg = Image.new('RGB', im.size, (255, 255, 255))
    box = ImageChops.difference(im, bg).getbbox() or (0, 0, im.width, im.height)
    box = (max(0, box[0] - 6), max(0, box[1] - 6), min(im.width, box[2] + 6), min(im.height, box[3] + 6))
    im = im.crop(box)
    return im.resize((PANEL_W, int(im.height * PANEL_W / im.width)), Image.LANCZOS), box


def to_px(ux, uy, crop, panel_h):
    """ACE user units -> composite pixels, given the crop box of the render."""
    sx = PANEL_W / (crop[2] - crop[0])
    sy = panel_h / (crop[3] - crop[1])
    # the render was 1200x700 CSS px over viewBox 116x66 -> uniform scale; reuse sx for both axes
    px = (ux - VB[0]) / VB[2] * (1200 * (crop[2] - crop[0]) / 2400) * sx / sx
    return px, 0


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
d.text((xl, y), 'v3 · 9/10', font=ft, fill=(20, 20, 20))
d.text((xl, y + 30), 'app constants, but rows advance diagonally —', font=fs, fill=(90, 90, 90))
d.text((xl, y + 52), 'the rack is a rhombus, not a triangle', font=fs, fill=(200, 30, 30))
d.text((xr, y), 'v4 · 10/10 (10/11 with the direction check)', font=ft, fill=(20, 20, 20))
d.text((xr, y + 30), 'lattice formula → exact triangle, back row vertical —', font=fs, fill=(90, 90, 90))
d.text((xr, y + 52), 'but the cue butt points at the rack', font=fs, fill=(200, 30, 30))
canvas.save('hard/pooltable-v3-v4.png')
print('wrote hard/pooltable-v3-v4.png %dx%d' % canvas.size)
