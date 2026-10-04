"""The plugin's GPU module (rs_apply, C++) against rml2ae/wgsl_apply.py (Python) on the fxlib effects.

Both run the same WGSL through wgpu with the same parameters; the C++ side is what the After Effects plugin uses
(RsShader packs `struct Params`, RsGpu uploads, renders and reads back). Every single-pass effect without extra
textures is rendered with each of its settings in fxref/renders.json, and the two 8-bit results are compared.

    python rml2ae/plugin/tools/oracle_check.py <path to rs_apply> [slug ...] [--max-diff N]
    python rml2ae/plugin/tools/oracle_check.py --compile-all

Exit code 1 when a result differs by more than --max-diff levels (default 0) or a render fails.
--compile-all renders every fxlib effect (multi-pass and map effects included) once with wgsl_apply.py at its
defaults on a small image: does each WGSL compile and run on this machine's GPU backend (on Windows, Direct3D 12
with the FXC compiler)?
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)

import numpy as np                                          # noqa: E402
from PIL import Image                                       # noqa: E402

from rml2ae.ae2rml import fxlib                             # noqa: E402

SRC = os.path.join(ROOT, "rml2ae", "ae2rml", "fxref", "src", "src_premult.png")


def write_rgba(path, img):
    a = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    with open(path, "wb") as f:
        f.write(struct.pack("<II", a.shape[1], a.shape[0]))
        f.write(a.tobytes())


def read_rgba(path):
    with open(path, "rb") as f:
        w, h = struct.unpack("<II", f.read(8))
        return np.frombuffer(f.read(), dtype=np.uint8).reshape(h, w, 4)


def compile_all():
    tmp = tempfile.mkdtemp(prefix="oracle_")
    small = os.path.join(tmp, "small.png")
    Image.open(SRC).resize((160, 90)).save(small)
    mp = os.path.join(ROOT, "rml2ae", "ae2rml", "fxref", "src", "map.png")
    slugs = sorted(f[:-5] for f in os.listdir(fxlib.LIB) if f.endswith(".json") and not f.startswith("_"))
    bad = []
    for slug in slugs:
        try:
            fxlib.run_offline(slug, {}, small, os.path.join(tmp, slug + ".png"), map_png=mp)
            print(f"ok   {slug}")
        except Exception as e:
            msg = " ".join(str(e).split())
            bad.append(slug)
            print(f"FAIL {slug}: {msg[-400:]}")
    print(f"compile-all: {len(slugs)} effect(s), {len(bad)} failing: {' '.join(bad)}")
    return 1 if bad else 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--compile-all":
        return compile_all()
    rs_apply, rest = argv[0], argv[1:]
    max_diff = 0
    if "--max-diff" in rest:
        i = rest.index("--max-diff")
        max_diff = int(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]
    renders = json.load(open(os.path.join(ROOT, "rml2ae", "ae2rml", "fxref", "renders.json"), encoding="utf-8"))
    src = Image.open(SRC)
    tmp = tempfile.mkdtemp(prefix="oracle_")
    src_raw = os.path.join(tmp, "src.rgba")
    write_rgba(src_raw, src)
    bad = checked = 0
    for r in renders:
        slug = r["slug"]
        if slug == "none" or (rest and slug not in rest):
            continue
        m = fxlib.manifest(slug)
        if int(m.get("passes", 1)) != 1 or m.get("textures"):
            continue
        name = f"{slug}_{r['k']}"
        vals = fxlib.values_for(m, r["vals"], src.size)
        vals["passIndex"] = 0.0
        py_png = os.path.join(tmp, name + ".png")
        try:
            fxlib.run_offline(slug, r["vals"], SRC, py_png)
        except Exception as e:      # the shader does not compile on this platform: rs_apply must say the same
            py_png = None
            py_err = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
        args = [rs_apply, fxlib.wgsl_path(m), src_raw, os.path.join(tmp, name + ".rgba")]
        for k, v in vals.items():
            if k == "size":
                continue
            v = v if isinstance(v, (list, tuple)) else [v]
            args += ["--set", f"{k}=" + ",".join(repr(float(x)) for x in v)]
        p = subprocess.run(args, capture_output=True, text=True)
        checked += 1
        if py_png is None:
            bad += 1
            print(f"FAIL {name}: wgsl_apply: {py_err[:300]} | rs_apply exit {p.returncode}")
            continue
        if p.returncode != 0:
            bad += 1
            print(f"FAIL {name}: rs_apply exit {p.returncode}: {p.stderr.strip()[:400]}")
            continue
        a = read_rgba(os.path.join(tmp, name + ".rgba")).astype(int)
        b = np.asarray(Image.open(py_png).convert("RGBA")).astype(int)
        d = int(np.abs(a - b).max())
        if d > max_diff:
            bad += 1
            print(f"DIFF {name}: max {d} levels, {(np.abs(a - b).max(-1) > 0).mean() * 100:.2f} % of pixels")
        else:
            print(f"ok   {name}: max {d}")
    print(f"oracle_check: {checked} render(s), {bad} problem(s)")
    return 1 if bad or not checked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
