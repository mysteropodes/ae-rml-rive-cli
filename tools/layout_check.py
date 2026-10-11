"""Does rml2ae's layout solver place the boxes where the Rive runtime does? (CI, needs the Rive CLI)

    python tools/layout_check.py rml2ae/tests/cases/layout [more cases...]

Each case is rendered as is and as layout.flatten() writes it (every LayoutComponent a Node at the solved place, its
paints on a rectangle the solved size); the two screenshots must match. On a mismatch, the bounding box of each colour
in both images tells which box moved.
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from rml2ae import layout as L     # noqa: E402


def shot(case, png):
    res = subprocess.run(["rive", case, f"--screenshot={png}"], capture_output=True, text=True)
    if res.returncode or not os.path.exists(png):
        raise RuntimeError(f"rive {case} --screenshot failed:\n{res.stdout}\n{res.stderr}")
    return np.asarray(Image.open(png).convert("RGB")).astype(int)


def boxes(img):
    flat = img.reshape(-1, 3)
    cols, counts = np.unique(flat, axis=0, return_counts=True)
    out = {}
    for c, n in zip(cols, counts):
        if n < 50:
            continue
        ys, xs = np.nonzero(np.all(img == c, axis=2))
        out["%02X%02X%02X" % tuple(c)] = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, n)
    return out


def check(case):
    tmp = tempfile.mkdtemp()
    flat = os.path.join(tmp, os.path.basename(case.rstrip("/")))
    L.flatten(case, flat)
    a = shot(case, os.path.join(tmp, "orig.png"))
    b = shot(flat, os.path.join(tmp, "flat.png"))
    if a.shape != b.shape:
        print(f"{case}: sizes differ {a.shape} {b.shape}")
        return False
    bad = np.any(np.abs(a - b) > 24, axis=2)
    frac = bad.mean()
    print(f"{case}: {bad.sum()} pixels differ ({frac:.4%})")
    if frac <= 0.002:
        return True
    ba, bb = boxes(a), boxes(b)
    for k in sorted(set(ba) | set(bb)):
        if ba.get(k, (0,) * 4)[:4] != bb.get(k, (0,) * 4)[:4]:
            print(f"  {k}  rive {ba.get(k)}  solved {bb.get(k)}")
    return False


if __name__ == "__main__":
    ok = all([check(c) for c in sys.argv[1:]])
    sys.exit(0 if ok else 1)
