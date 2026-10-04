"""AE property timelines -> Rive keys.

Three cases per property (post-expression):
  static  — no keys (or one), or an expression that does not depend on time: one value;
  keys    — AE keyframes converted exactly: hold / linear / bezier ease (influence + speed -> CubicEaseInterpolator,
            a speed bump between two equal values -> CubicValueInterpolator);
  frames  — sampled at every Rive frame (time-dependent expression, curved motion path, keys before 0…), then
            reduced to poses + eases by motion/reduce_keys.py.
Unit changes are affine (degrees -> radians, % -> fractions, -anchor), which keeps an ease curve exact.
"""
import math
import os
import sys

from .aexpr import OVERRIDES, ExprError, ShapeValue, raw_value, to_js, tonum, unwrap
from .util import ROOT, clean

try:
    for _p in (os.path.join(ROOT, "01_RIV_LIBRARY"), ROOT):
        if os.path.isdir(os.path.join(_p, "motion")) and _p not in sys.path:
            sys.path.insert(0, _p)
    from motion.reduce_keys import reduce as _reduce          # type: ignore
except Exception:                                            # pragma: no cover - dist without motion/
    _reduce = None

LIN, BEZ, HOLD = 6612, 6613, 6614


def _itype(v):
    try:
        return int(v)
    except Exception:
        return BEZ


def comp(v, dim):
    v = unwrap(v)
    if dim is None:
        return tonum(v) if not isinstance(v, list) else tonum(v[0])
    if isinstance(v, list):
        return tonum(v[dim]) if dim < len(v) else 0.0
    return tonum(v)


def reduce_track(dense, tol=0.01, smooth=False):
    """[(frame, value)] -> [(frame, value, interp, ease)].
    smooth=True: poses + cubic eases (motion/reduce_keys.py — slower, for the few transform tracks an animator edits);
    default: Douglas-Peucker linear keys (fast, for sampled paths / colours / expressions)."""
    if not dense:
        return []
    vals = [v for _, v in dense]
    span = max(vals) - min(vals)
    if span < 1e-9:
        return [(dense[0][0], vals[0], "hold", None)]
    if smooth and _reduce is not None and len(dense) <= 1200:
        try:
            return _reduce(dense, tol=tol)
        except Exception:
            pass
    keep = douglas_peucker(dense, max(1e-6, span * 0.0015))
    # step tracks (mostly equal consecutive samples: a mask drawn one frame in N, a vertex parked between its
    # drawings) gain nothing from curves and cost the fitter one trial per step — broadcast-test's 1 422 frame-by-frame masks
    still = sum(1 for a, b in zip(vals, vals[1:]) if a == b)
    if len(keep) > 3 and still < 0.5 * (len(vals) - 1):
        # eased motion sampled frame by frame: a linear key per frame in the curves, a few cubic keys instead
        curves = fit_curves(dense, max(1e-6, span * 0.0015))
        if curves is not None and len(curves) < len(keep):
            return curves
    out = [(dense[i][0], dense[i][1], "linear", None) for i in keep[:-1]]
    out.append((dense[keep[-1]][0], dense[keep[-1]][1], "hold", None))
    # flat runs -> holds (no drift between equal keys)
    res = []
    for k, (f, v, interp, e) in enumerate(out):
        if k + 1 < len(out) and abs(out[k + 1][1] - v) < 1e-9:
            interp = "hold"
        res.append((f, v, interp, e))
    return res


_X1 = (0.0, 0.1, 0.2, 1 / 3, 0.45, 0.6, 0.75, 0.9)
_X2 = (0.1, 0.25, 0.4, 0.55, 2 / 3, 0.8, 0.9, 1.0)


_BASIS = {}


def _basis_for(u):
    """for u (1 × k, 0..1) and every (x1, x2) of the grid: the Bézier parameter s with x(s) = u and the least-squares
    terms of the two free ordinates. Depends on the sample times only, not on the values"""
    import numpy as np
    X1 = np.repeat(np.array(_X1), len(_X2))[:, None]
    X2 = np.tile(np.array(_X2), len(_X1))[:, None]
    lo, hi = np.zeros((X1.shape[0], u.shape[1])), np.ones((X1.shape[0], u.shape[1]))
    for _ in range(24):                           # x(s) = u, the timing curve being monotonic
        s = (lo + hi) / 2
        m = 1 - s
        x = 3 * m * m * s * X1 + 3 * m * s * s * X2 + s ** 3
        lo = np.where(x < u, s, lo)
        hi = np.where(x < u, hi, s)
    s = (lo + hi) / 2
    m = 1 - s
    b1, b2 = 3 * m * m * s, 3 * m * s * s
    a11, a12, a22 = (b1 * b1).sum(1), (b1 * b2).sum(1), (b2 * b2).sum(1)
    det = a11 * a22 - a12 * a12
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    return m ** 3, s ** 3, b1, b2, a11, a12, a22, det


def _basis(L):
    """_basis_for evenly spaced samples 0..L, cached (the curve fitter asks for the same lengths over and over: the
    24-step inversion per trial made broadcast-test's 400 000-key project take 37 minutes)"""
    b = _BASIS.get(L)
    if b is None:
        import numpy as np
        b = _basis_for((np.arange(L + 1) / L)[None, :])
        if L <= 4096:
            if len(_BASIS) > 3000:
                _BASIS.clear()
            _BASIS[L] = b
    return b


def fit_curves(dense, tol):
    """[(frame, value)] -> [(frame, value, interp, ease)] with cubic segments, or None.
    Greedy: each segment runs as far as one Rive cubic (ease x1/x2 from a grid, y1/y2 by least squares) stays within
    `tol` of EVERY sampled frame, so the keys say what the samples say — only with a curve between keys instead of a
    straight line per frame. A segment of equal ends is a CubicValueInterpolator (a bump), a flat one a hold."""
    try:
        import numpy as np
    except ImportError:
        return None
    n = len(dense)
    if n < 4 or n > 20000:
        return None
    F = np.array([float(f) for f, _ in dense])
    V = np.array([float(v) for _, v in dense])
    X1 = np.repeat(np.array(_X1), len(_X2))[:, None]
    X2 = np.tile(np.array(_X2), len(_X1))[:, None]

    def fit(i, j):
        """(max error, x1, Y1, x2, Y2) of the best cubic through samples i..j (absolute Y values)"""
        seg = F[i:j + 1] - F[i]
        if abs(seg[-1] - (j - i) * (seg[1] if j > i else 0)) < 1e-9 and np.allclose(np.diff(seg), seg[1]):
            m3, s3, b1, b2, a11, a12, a22, det = _basis(j - i)      # evenly spaced frames: cached per length
        else:
            m3, s3, b1, b2, a11, a12, a22, det = _basis_for((seg / seg[-1])[None, :])
        R = V[None, i:j + 1] - V[i] * m3 - V[j] * s3
        r1, r2 = (b1 * R).sum(1), (b2 * R).sum(1)
        Y1 = (r1 * a22 - r2 * a12) / det
        Y2 = (a11 * r2 - a12 * r1) / det
        E = np.abs(R - Y1[:, None] * b1 - Y2[:, None] * b2).max(1)
        g = int(np.argmin(E))
        return float(E[g]), float(X1[g, 0]), float(Y1[g]), float(X2[g, 0]), float(Y2[g])

    def ok(i, j):
        if j - i < 2:
            return True, None
        if np.abs(V[i:j + 1] - V[i]).max() <= tol and abs(V[j] - V[i]) <= tol:
            return True, "flat"
        lin = V[i] + (V[j] - V[i]) * (F[i:j + 1] - F[i]) / (F[j] - F[i])
        if np.abs(V[i:j + 1] - lin).max() <= tol:
            return True, "linear"
        r = fit(i, j)
        return r[0] <= tol, r

    out, i = [], 0
    while i < n - 1:
        best, best_fit = i + 1, None
        step = 2
        j = i + 2
        fail = None
        while j <= n - 1:                          # grow while it fits, then bisect the first failure
            good, r = ok(i, j)
            if not good:
                fail = j
                break
            best, best_fit = j, r
            j = i + step * 2
            step *= 2
        if fail is not None:
            a, b = best, fail
            while b - a > 1:
                mid = (a + b) // 2
                good, r = ok(i, mid)
                if good:
                    a, best, best_fit = mid, mid, r
                else:
                    b = mid
        elif best < n - 1:
            good, r = ok(i, n - 1)
            if good:
                best, best_fit = n - 1, r
        f0, v0, v1 = dense[i][0], float(V[i]), float(V[best])
        if best_fit is None or best_fit == "linear":
            out.append((f0, v0, "linear", None))
        elif best_fit == "flat":
            out.append((f0, v0, "hold", None))
        else:
            _e, x1, Y1, x2, Y2 = best_fit
            if abs(v1 - v0) > 1e-9 * max(1.0, abs(v0)):
                out.append((f0, v0, "cubic", (round(x1, 5), round((Y1 - v0) / (v1 - v0), 5),
                                              round(x2, 5), round((Y2 - v0) / (v1 - v0), 5))))
            else:
                out.append((f0, v0, "cubicValue", (round(x1, 5), Y1, round(x2, 5), Y2)))
        i = best
    out.append((dense[-1][0], float(V[-1]), "hold", None))
    return out


def douglas_peucker(pts, tol):
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = {0, n - 1}
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        fi, vi = pts[i]
        fj, vj = pts[j]
        best, bk = -1.0, -1
        for k in range(i + 1, j):
            fk, vk = pts[k]
            u = (fk - fi) / (fj - fi) if fj != fi else 0.0
            d = abs(vk - (vi + (vj - vi) * u))
            if d > best:
                best, bk = d, k
        if best > tol:
            keep.add(bk)
            stack.append((i, bk))
            stack.append((bk, j))
    return sorted(keep)


class Timeline:
    """time frame of one artboard: Rive fps, frame count, the expression engine and the report"""

    def __init__(self, conv, comp_item, fps):
        self.conv = conv
        self.comp = comp_item
        self.fps = fps
        self.duration = float(comp_item.duration)
        self.nframes = max(1, int(round(self.duration * fps)))
        self.engine = conv.engine
        self.scope = clean(comp_item.name)


_POSTERIZE = {}


def posterize_fps(L):
    """frame rate of an enabled Posterize Time effect on layer L (None without one). Measured in AE 26.5: it steps
    EVERYTHING the layer animates — its transform too, not only its content (a square moved by its position alone
    stepped at 4 fps)"""
    if L is None:
        return None
    k = id(L)
    if k not in _POSTERIZE:
        f = None
        try:
            for fx in (L.effects.properties if L.effects is not None else []):
                if getattr(fx, "enabled", True) and getattr(fx, "match_name", "") == "ADBE Posterize Time":
                    for q in fx.properties:
                        if getattr(q, "match_name", "") == "ADBE Posterize Time-0001":
                            f = float(tonum(q.value))
        except Exception:
            f = None
        _POSTERIZE[k] = f if f and f > 0 else None
    return _POSTERIZE[k]


class AProp:
    """one AE property on the converter's side"""

    def __init__(self, tl, p, L, what="", stretch=1.0):
        self.tl, self.p, self.L, self.what = tl, p, L, what
        self.stretch = stretch
        self.mode = "static"
        self.value = None
        self.frames = None
        self.keys = []
        self.hold = False
        self._resolve()
        self._posterize()

    def _posterize(self):
        """Posterize Time on the layer: the value held at each 1/f step, keyed as holds"""
        f = posterize_fps(self.L)
        tl = self.tl
        if not f or self.mode == "static" or f >= tl.fps - 1e-6:
            return
        out = []
        for fr in range(tl.nframes + 1):
            tq = math.floor(fr / tl.fps * f + 1e-6) / f
            if self.mode == "frames":
                out.append(self.frames[max(0, min(len(self.frames) - 1, int(round(tq * tl.fps))))])
            else:
                out.append(to_js(_at(self.p, tq)))
        self.mode, self.frames, self.hold = "frames", out, True

    # ------------------------------------------------------------------ analysis
    def _resolve(self):
        p, tl = self.p, self.tl
        if p is None:
            return
        try:
            ks = list(p.keyframes)
        except Exception:
            ks = []
        o = OVERRIDES.get(id(p))
        if o is not None and not tl.engine.has_expr(p):
            # an Essential Property overridden by the precomp instance: its value (keys sampled per frame)
            if not o[1]:
                self.value = to_js(o[0](None))
                return
            vals = [to_js(o[0](f / tl.fps)) for f in range(tl.nframes + 1)]
            self.value = vals[0]
            if not all(_same(vals[0], v) for v in vals[1:]):
                self.mode, self.frames = "frames", vals
            return
        if tl.engine.has_expr(p):
            kind, res = tl.engine.analyze(p, self.L, tl.comp, tl.fps, tl.nframes)
            if kind == "static":
                self.value = res
                return
            if kind == "frames":
                vals = [unwrap(v) for v in res]
                if all(_same(vals[0], v) for v in vals[1:]):
                    self.value = vals[0]
                    return
                self.mode, self.frames = "frames", vals
                self.value = vals[0]
                return
            tl.conv.report.add(tl.scope, "approx", self.what, f"expression kept out ({res}): pre-expression value used "
                                                             f"— `{(p.expression or '').strip()[:90]}`")
            tl.conv.failed_expr.append((tl.scope, self.what, p.expression, res))
        if len(ks) >= 2:
            self.keys = ks
            self.value = to_js(ks[0].value)
            if self._needs_sampling(ks):
                self.mode = "frames"
                self.frames = [to_js(_at(p, f / tl.fps)) for f in range(tl.nframes + 1)]
            else:
                self.mode = "keys"
            return
        if len(ks) == 1:
            self.value = to_js(ks[0].value)
            return
        try:
            self.value = to_js(raw_value(p, self.L))
        except Exception:
            self.value = None

    def _needs_sampling(self, ks):
        tl = self.tl
        if self.stretch <= 0:
            return True
        if ks[0].time < -1e-6:
            return True
        frames = [round(k.time * tl.fps) for k in ks]
        if len(set(frames)) != len(frames):
            return True
        if getattr(self.p, "is_spatial", False):
            for a, b in zip(ks, ks[1:]):
                if _itype(a.out_interpolation_type) == HOLD:
                    continue
                if not _straight(a, b):
                    return True
        return False

    @property
    def animated(self):
        return self.mode != "static"

    def at(self, t):
        """post-expression value at time t (JS form)"""
        if self.mode == "frames":
            f = t * self.tl.fps
            i = max(0, min(len(self.frames) - 1, int(math.floor(f))))
            j = min(len(self.frames) - 1, i + 1)
            u = f - i
            a, b = unwrap(self.frames[i]), unwrap(self.frames[j])
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                return a + (b - a) * u
            if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
                return [tonum(x) + (tonum(y) - tonum(x)) * u for x, y in zip(a, b)]
            return a
        if self.mode == "keys":
            return to_js(_at(self.p, t))
        return self.value

    def static(self, dim=None, default=0.0):
        v = self.value
        if v is None:
            return default
        if isinstance(v, (ShapeValue, str)):
            return v
        return comp(v, dim) if (dim is not None or not isinstance(v, list)) else [tonum(x) for x in v]

    def times(self):
        """key times (seconds) worth sampling a composite value at"""
        if self.mode == "keys":
            return [k.time for k in self.keys]
        if self.mode == "frames":
            return [f / self.tl.fps for f in range(len(self.frames))]
        return []

    # ------------------------------------------------------------------ Rive keys
    def rive_keys(self, dim=None, a=1.0, b=0.0, tol=0.01):
        """[(frame, value, interp, ease)] of component `dim`, value mapped v -> a*v + b"""
        if self.mode == "static":
            return []
        if self.mode == "frames":
            dense = [(f, comp(v, dim) * a + b) for f, v in enumerate(self.frames)]
            if self.hold:
                out, last = [], None
                for f, v in dense:
                    if last is None or abs(v - last) > 1e-9:
                        out.append((f, v, "hold", None))
                        last = v
                return out
            return reduce_track(dense, tol, smooth=True)
        return exact_keys(self.keys, dim, a, b, self.tl.fps, getattr(self.p, "is_spatial", False), self.stretch)


def _same(a, b, eps=1e-6):
    a, b = unwrap(a), unwrap(b)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y, eps) for x, y in zip(a, b))
    if isinstance(a, ShapeValue) and isinstance(b, ShapeValue):
        return (len(a.v) == len(b.v) and all(_same(x, y) for x, y in zip(a.v, b.v)) and
                all(_same(x, y) for x, y in zip(a.i, b.i)) and all(_same(x, y) for x, y in zip(a.o, b.o)))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= eps * max(1.0, abs(a))
    return a == b


def _at(p, t):
    try:
        return p.value_at_time(t)
    except Exception:
        return p.value


def _straight(a, b):
    """a spatial segment with no curvature (tangents zero or along the chord)"""
    try:
        va, vb = list(a.value), list(b.value)
        ot = a.out_spatial_tangent or [0, 0, 0]
        it = b.in_spatial_tangent or [0, 0, 0]
    except Exception:
        return True
    chord = [vb[i] - va[i] for i in range(min(len(va), len(vb)))]
    L = math.sqrt(sum(c * c for c in chord))
    for tg in (ot, it):
        n = math.sqrt(sum(x * x for x in tg[:len(chord)]))
        if n < 1e-3:
            continue
        if L < 1e-6:
            return False
        cross2 = (tg[0] * chord[1] - tg[1] * chord[0]) if len(chord) >= 2 else 0.0
        if abs(cross2) / (n * L) > 1e-3:
            return False
    return True


def exact_keys(ks, dim, a, b, fps, spatial, stretch=1.0):
    out = []
    for i, k in enumerate(ks):
        f = k.time * fps
        v0 = comp(to_js(k.value), dim)
        if i == len(ks) - 1:
            out.append((f, v0 * a + b, "hold", None))
            break
        nk = ks[i + 1]
        ot, it = _itype(k.out_interpolation_type), _itype(nk.in_interpolation_type)
        v1 = comp(to_js(nk.value), dim)
        if ot == HOLD:
            out.append((f, v0 * a + b, "hold", None))
            continue
        if ot == LIN and it == LIN:
            out.append((f, v0 * a + b, "linear", None))
            continue
        dt = (nk.time - k.time) / max(1e-9, stretch)            # ease speeds are per layer second
        if spatial:
            pa, pb = to_js(k.value), to_js(nk.value)
            delta = math.sqrt(sum((tonum(y) - tonum(x)) ** 2 for x, y in zip(pa, pb))) if isinstance(pa, list) else (v1 - v0)
            sign_ok = True
            di = 0
        else:
            delta = v1 - v0
            sign_ok = True
            di = dim or 0
        try:
            oe = k.out_temporal_ease[min(di, len(k.out_temporal_ease) - 1)]
            ie = nk.in_temporal_ease[min(di, len(nk.in_temporal_ease) - 1)]
            so, io = float(oe.speed), float(oe.influence)
            si, ii = float(ie.speed), float(ie.influence)
        except Exception:
            so = si = 0.0
            io = ii = 33.3333
        x1 = min(1.0, max(0.001, io / 100.0))
        x2 = 1.0 - min(1.0, max(0.001, ii / 100.0))
        comp_delta = v1 - v0
        if abs(delta) < 1e-9 or abs(comp_delta) < 1e-9:
            if abs(so) < 1e-9 and abs(si) < 1e-9 or spatial:
                out.append((f, v0 * a + b, "linear", None))
            else:
                # the value leaves and comes back: a curve through values (units of the property, mapped)
                y1v = v0 + (so * x1 * dt if ot != LIN else 0.0)
                y2v = v1 - (si * (1 - x2) * dt if it != LIN else 0.0)
                out.append((f, v0 * a + b, "cubicValue", (x1, y1v * a + b, x2, y2v * a + b)))
            continue
        if spatial:
            # speed is along the path: the same progress for every axis of a straight segment
            y1 = x1 if ot == LIN else so * x1 * dt / delta
            y2 = x2 if it == LIN else 1.0 - si * (1 - x2) * dt / delta
        else:
            y1 = x1 if ot == LIN else so * x1 * dt / delta
            y2 = x2 if it == LIN else 1.0 - si * (1 - x2) * dt / delta
        if not sign_ok:
            y1, y2 = x1, x2
        if abs(x1 - y1) < 1e-3 and abs(x2 - y2) < 1e-3:
            out.append((f, v0 * a + b, "linear", None))
        else:
            out.append((f, v0 * a + b, "cubic", (round(x1, 5), round(y1, 5), round(x2, 5), round(y2, 5))))
    return out


def _flat(v):
    v = to_js(v)
    if isinstance(v, ShapeValue):
        return [c for p in v.v + v.i + v.o for c in p]
    if isinstance(v, list):
        out = []
        for x in v:
            out += _flat(x) if isinstance(x, (list, ShapeValue)) else [tonum(x)]
        return out
    if isinstance(v, (int, float)):
        return [float(v)]
    return []


def _solve_s(x, x1, x2):
    lo, hi, s = 0.0, 1.0, x
    for _ in range(50):
        xx = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s ** 2 * x2 + s ** 3
        if abs(xx - x) < 1e-9:
            break
        if xx < x:
            lo = s
        else:
            hi = s
        s = (lo + hi) / 2
    return s


def fit_progress(p, k, nk, x1, x2, samples=10):
    """(y1, y2) of the ease AE actually applies on this segment, measured on py-aep's interpolation (the speed units
    of paths and colours are not the property's units): progress is least-squares fitted with x1, x2 fixed."""
    a, b = _flat(k.value), _flat(nk.value)
    if not a or len(a) != len(b):
        return None
    d = [y - x for x, y in zip(a, b)]
    dd = sum(x * x for x in d)
    if dd < 1e-12:
        return None
    rows = []
    for j in range(1, samples + 1):
        x = j / (samples + 1)
        t = k.time + x * (nk.time - k.time)
        v = _flat(_at(p, t))
        if len(v) != len(a):
            return None
        u = sum((vi - ai) * di for vi, ai, di in zip(v, a, d)) / dd
        s = _solve_s(x, x1, x2)
        rows.append((3 * (1 - s) ** 2 * s, 3 * (1 - s) * s * s, u - s ** 3))
    s11 = sum(r[0] * r[0] for r in rows)
    s12 = sum(r[0] * r[1] for r in rows)
    s22 = sum(r[1] * r[1] for r in rows)
    b1 = sum(r[0] * r[2] for r in rows)
    b2 = sum(r[1] * r[2] for r in rows)
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12:
        return None
    return (b1 * s22 - b2 * s12) / det, (s11 * b2 - s12 * b1) / det


def progress_keys(ks, fps, stretch=1.0, p=None):
    """For values interpolated along one eased progress (paths, colours, gradients): per segment
    (frame, interp, ease) where the ease maps time -> progress 0..1."""
    out = []
    for i, k in enumerate(ks):
        f = k.time * fps
        if i == len(ks) - 1:
            out.append((f, "hold", None))
            break
        nk = ks[i + 1]
        ot, it = _itype(k.out_interpolation_type), _itype(nk.in_interpolation_type)
        if ot == HOLD:
            out.append((f, "hold", None))
            continue
        if ot == LIN and it == LIN:
            out.append((f, "linear", None))
            continue
        try:
            oe, ie = k.out_temporal_ease[0], nk.in_temporal_ease[0]
            so, io, si, ii = float(oe.speed), float(oe.influence), float(ie.speed), float(ie.influence)
        except Exception:
            so = si = 0.0
            io = ii = 33.3333
        x1 = min(1.0, max(0.001, io / 100.0))
        x2 = 1.0 - min(1.0, max(0.001, ii / 100.0))
        fitted = fit_progress(p, k, nk, x1, x2) if p is not None else None
        if fitted is not None:
            y1, y2 = fitted
        else:
            dt = (nk.time - k.time) / max(1e-9, stretch)
            y1 = x1 if ot == LIN else (so / 100.0) * x1 * dt
            y2 = x2 if it == LIN else 1.0 - (si / 100.0) * (1 - x2) * dt
            if abs(so) < 1e-9 and ot != LIN:
                y1 = 0.0
            if abs(si) < 1e-9 and it != LIN:
                y2 = 1.0
        if abs(x1 - y1) < 1e-3 and abs(x2 - y2) < 1e-3:
            out.append((f, "linear", None))
        else:
            out.append((f, "cubic", (round(x1, 5), round(y1, 5), round(x2, 5), round(y2, 5))))
    return out
