"""Crop white margins of KOMPAS raster exports and downscale (ignores thin stray lines).
usage: python crop.py <in.png> <out.png> [max_side=2400] [pad=40] [min_px=6]
"""
import sys
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
src, dst = sys.argv[1], sys.argv[2]
max_side = int(sys.argv[3]) if len(sys.argv) > 3 else 2400
pad = int(sys.argv[4]) if len(sys.argv) > 4 else 40
min_px = int(sys.argv[5]) if len(sys.argv) > 5 else 6
im = Image.open(src).convert("RGB")
a = np.asarray(im).astype(np.int16)
mask = (np.abs(a - 255).max(axis=2) > 12)
cols = np.where(mask.sum(axis=0) >= min_px)[0]
rows = np.where(mask.sum(axis=1) >= min_px)[0]
box = None
if len(cols) and len(rows):
    x1, x2, y1, y2 = cols[0], cols[-1] + 1, rows[0], rows[-1] + 1
    x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
    x2, y2 = min(im.width, x2 + pad), min(im.height, y2 + pad)
    box = (int(x1), int(y1), int(x2), int(y2))
    im = im.crop(box)
s = max(im.size)
if s > max_side:
    k = max_side / s
    im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
im.save(dst, optimize=True)
print(f"{dst}: bbox={box} size={im.size}")
