"""Oracle for the key conversion: Rive keys (hold / linear / cubic / cubicValue, evaluated here) vs After Effects'
interpolation (py-aep value_at_time, verified against AE), frame by frame, for every keyed transform / shape
property of the given projects.

    python -m rml2ae.ae2rml.check_keys a.aep b.aep … [--worst 15]
"""
import glob
import math
import sys

from .aexpr import _bez, to_js
from .keys import AProp, Timeline, comp


class _Conv:
    def __init__(self, project):
        from .aexpr import Engine
        self.engine = Engine(project)
        self.failed_expr = []

        class R:
            def add(self, *a):
                pass
        self.report = R()


def eval_keys(keys, f):
    ks = sorted(keys, key=lambda k: k[0])
    if f <= ks[0][0]:
        return ks[0][1]
    for a, b in zip(ks, ks[1:]):
        if a[0] <= f <= b[0]:
            if a[2] == "hold":
                return a[1] if f < b[0] else b[1]
            u = (f - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 1.0
            if a[2] == "linear":
                return a[1] + (b[1] - a[1]) * u
            if a[2] == "cubic":
                return a[1] + (b[1] - a[1]) * _bez(u, *a[3])
            if a[2] == "cubicValue":
                x1, y1, x2, y2 = a[3]
                lo, hi, s = 0.0, 1.0, u
                for _ in range(50):
                    xx = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s * s * x2 + s ** 3
                    if abs(xx - u) < 1e-9:
                        break
                    if xx < u:
                        lo = s
                    else:
                        hi = s
                    s = (lo + hi) / 2
                return (1 - s) ** 3 * a[1] + 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s * s * y2 + s ** 3 * b[1]
    return ks[-1][1]


TARGETS = ("ADBE Position", "ADBE Position_0", "ADBE Position_1", "ADBE Scale", "ADBE Rotate Z", "ADBE Opacity",
           "ADBE Anchor Point", "ADBE Vector Position", "ADBE Vector Scale", "ADBE Vector Rotation",
           "ADBE Vector Group Opacity", "ADBE Vector Rect Size", "ADBE Vector Ellipse Size", "ADBE Vector Trim Start",
           "ADBE Vector Trim End", "ADBE Vector Trim Offset", "ADBE Vector Stroke Width", "ADBE Vector Fill Opacity")


def walk(g):
    try:
        props = list(g.properties)
    except Exception:
        return
    for p in props:
        if type(p).__name__ == "Property":
            yield p
        else:
            yield from walk(p)


def check(path, results):
    import py_aep
    app = py_aep.parse(path)
    conv = _Conv(app.project)
    for c in app.project.compositions:
        fps = max(1, int(round(float(c.frame_rate or 25))))
        tl = Timeline(conv, c, fps)
        for L in c.layers:
            stretch = float(getattr(L, "stretch", 100.0) or 100.0) / 100.0
            for p in walk(L):
                if getattr(p, "match_name", "") not in TARGETS:
                    continue
                try:
                    if len(p.keyframes) < 2 or conv.engine.has_expr(p):
                        continue
                except Exception:
                    continue
                ap = AProp(tl, p, L, stretch=stretch)
                if ap.mode != "keys":
                    continue
                v0 = to_js(p.keyframes[0].value)
                dims = range(min(2, len(v0))) if isinstance(v0, list) else [None]
                for d in dims:
                    keys = ap.rive_keys(d)
                    t0, t1 = p.keyframes[0].time, p.keyframes[-1].time
                    vals = [comp(to_js(p.value_at_time(f / fps)), d) for f in range(int(t0 * fps), int(t1 * fps) + 1)]
                    rng = (max(vals) - min(vals)) if vals else 0.0
                    if rng < 1e-6:
                        continue
                    err = 0.0
                    for f in range(int(math.ceil(t0 * fps)), int(t1 * fps) + 1):
                        ae = comp(to_js(p.value_at_time(f / fps)), d)
                        rv = eval_keys(keys, f)
                        err = max(err, abs(ae - rv))
                    results.append((err / rng, err, path.split("/")[-1], c.name, L.name, p.match_name, d,
                                    getattr(p, "is_spatial", False)))


def main(argv):
    worst = 15
    files = []
    i = 0
    while i < len(argv):
        if argv[i] == "--worst":
            worst = int(argv[i + 1])
            i += 2
            continue
        files += sorted(glob.glob(argv[i]))
        i += 1
    results = []
    for f in files:
        try:
            check(f, results)
        except Exception as ex:
            print("!!", f, ex)
    if not results:
        print("no keyed property checked")
        return 0
    results.sort(reverse=True)
    rel = [r[0] for r in results]
    print(f"{len(results)} keyed tracks · max rel error {max(rel):.4f} · median {sorted(rel)[len(rel) // 2]:.5f} · "
          f"> 1 %: {sum(1 for r in rel if r > 0.01)}")
    for r in results[:worst]:
        print(f"  {r[0]:.4f} ({r[1]:.3f}) {r[2]} / {r[3]} / {r[4]} / {r[5]}[{r[6]}]{' spatial' if r[7] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
