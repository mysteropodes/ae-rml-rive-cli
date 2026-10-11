"""Print the bounding box of every colour that covers more than 50 pixels in a PNG (diagnostics for layout cases)."""
import sys
from collections import defaultdict

from PIL import Image

im = Image.open(sys.argv[1]).convert("RGB")
w, h = im.size
px = im.load()
boxes = defaultdict(lambda: [w, h, -1, -1, 0])
for y in range(h):
    for x in range(w):
        b = boxes[px[x, y]]
        b[0], b[1], b[2], b[3], b[4] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y), b[4] + 1
print("size", w, h)
for c, b in sorted(boxes.items(), key=lambda kv: -kv[1][4]):
    if b[4] > 50:
        print("%02X%02X%02X" % c, "x0=%d y0=%d x1=%d y1=%d n=%d" % (b[0], b[1], b[2] + 1, b[3] + 1, b[4]))
