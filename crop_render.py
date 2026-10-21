"""crop_render.py - crop a render to its non-white content, measured from pixels.

Crop-by-XML was tried first and failed (KiCad emits mixed relative/absolute path commands), so the box is taken
from the raster: no assumptions about the exporter's path syntax.
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
img = Image.open(src).convert('RGB')
bg = Image.new('RGB', img.size, (255, 255, 255))
diff = ImageChops.difference(img, bg)
box = diff.getbbox()
if box:
    pad = 12
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    img = img.crop(box)
img.save(dst)
print('cropped %s -> %s  %dx%d' % (src.name, dst.name, img.width, img.height))
