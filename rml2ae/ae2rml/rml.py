"""A tiny RML writer: elements, keyed animations (with their interpolators) and serialization.

Property keys come from rml2ae/schema.json (the Rive core definitions), so a keyed property is always named by
the key the runtime reads.
"""
import json
import math
import os

from .util import PKG, argb, esc, fmt

SCHEMA = json.load(open(os.path.join(PKG, "schema.json"), encoding="utf-8"))
TYPES = SCHEMA["types"]
_PK = {}


def prop_key(tag, name):
    """Rive property key of `name` on element `tag` (inherited properties included)."""
    k = (tag, name)
    if k in _PK:
        return _PK[k]
    t = TYPES.get(tag)
    chain = [tag] + (t["extends"] if t else [])
    for ty in chain:
        tt = TYPES.get(ty)
        if not tt:
            continue
        for p in tt.get("properties", []):
            if p["name"] == name:
                _PK[k] = p["key"]
                return p["key"]
    raise KeyError(f"{tag}.{name} is not a Rive property")


class E:
    """One RML element. Attributes keep their insertion order; `name` and `id` are written last."""
    __slots__ = ("tag", "attrs", "children", "comments", "id", "name")

    def __init__(self, tag, id=None, name=None, **attrs):
        self.tag = tag
        self.id = id
        self.name = name
        self.attrs = {}
        self.children = []
        self.comments = []            # "ae: …" lines written right before the element
        for k, v in attrs.items():
            if v is not None:
                self.attrs[k] = v

    def set(self, k, v):
        if v is None:
            self.attrs.pop(k, None)
        else:
            self.attrs[k] = v
        return self

    def add(self, child):
        if child is not None:
            self.children.append(child)
        return child

    def insert(self, i, child):
        if child is not None:
            self.children.insert(i, child)
        return child

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()

    def write(self, out, depth=0):
        pad = "  " * depth
        for c in self.comments:
            out.append(f"{pad}<!-- {c.replace('--', '- -')} -->")
        parts = [self.tag]
        for k, v in self.attrs.items():
            parts.append(f'{k}="{attr(v)}"')
        if self.name is not None:
            parts.append(f'name="{esc(self.name)}"')
        if self.id is not None:
            parts.append(f'id="{self.id}"')
        head = " ".join(parts)
        if not self.children:
            out.append(f"{pad}<{head}/>")
            return
        out.append(f"{pad}<{head}>")
        for c in self.children:
            c.write(out, depth + 1)
        out.append(f"{pad}</{self.tag}>")


def attr(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return fmt(v)
    if isinstance(v, int):
        return str(v)
    return esc(v)


# ------------------------------------------------------------------ animations
class Track:
    """Keys of one (object, property): kind double | color | string | bool | id | uint.
    keys = [(frame, value, interp, ease)] with interp hold|linear|cubic|cubicValue and ease (x1, y1, x2, y2)."""
    __slots__ = ("kind", "keys")

    def __init__(self, kind, keys):
        self.kind = kind
        self.keys = keys


class Anim:
    def __init__(self, id, name, fps, duration, loop="loop"):
        self.id = id
        self.name = name
        self.fps = fps
        self.duration = duration
        self.loop = loop
        self.tracks = {}                  # (object id, property key) -> Track
        self.order = []

    def put(self, obj_id, tag, prop, keys, kind="double"):
        if not keys:
            return
        pk = prop_key(tag, prop)
        k = (obj_id, pk)
        if k not in self.tracks:
            self.order.append(k)
        self.tracks[k] = Track(kind, keys)

    def element(self):
        la = E("LinearAnimation", id=self.id, name=self.name, fps=int(self.fps), duration=int(self.duration),
               loopValue=self.loop)
        by_obj = {}
        for (oid, pk) in self.order:
            by_obj.setdefault(oid, []).append(pk)
        for oid, pks in by_obj.items():
            ko = la.add(E("KeyedObject", objectId=oid))
            for pk in pks:
                tr = self.tracks[(oid, pk)]
                kp = ko.add(E("KeyedProperty", propertyKey=pk))
                for frame, value, interp, ease in prune(tr.kind, dedupe(snap(tr.kind, tr.keys))):
                    kp.add(keyframe(tr.kind, frame, value, interp, ease))
        return la


_FRAC = 1e-3


def _bez(p0, p1, p2, p3, s):
    m = 1 - s
    return m * m * m * p0 + 3 * m * m * s * p1 + 3 * m * s * s * p2 + s * s * s * p3


def _bez_x(x1, x2, x):
    """parameter s where the timing curve (0, x1, x2, 1) reaches x"""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        s = (lo + hi) / 2
        if _bez(0.0, x1, x2, 1.0, s) < x:
            lo = s
        else:
            hi = s
    return (lo + hi) / 2


def _split(p, s0, s1):
    """control points of the part s0..s1 of the cubic p (4 numbers): P(s0) + P'(s0)(s1-s0)/3, P(s1) - P'(s1)(s1-s0)/3"""
    def d(s):
        m = 1 - s
        return 3 * (m * m * (p[1] - p[0]) + 2 * m * s * (p[2] - p[1]) + s * s * (p[3] - p[2]))
    q0, q3 = _bez(*p, s0), _bez(*p, s1)
    h = (s1 - s0) / 3
    return q0, q0 + d(s0) * h, q3 - d(s1) * h, q3


def _vec(kind, v):
    if kind == "color":
        if isinstance(v, str):
            return [int(v[i:i + 2], 16) for i in (0, 2, 4, 6)]
        return [c * 255 for c in (lambda c: [c[3] if len(c) > 3 else 1.0] + list(c[:3]))(list(v))]
    return [float(v)]


def _unvec(kind, x):
    if kind == "color":
        return "%02X%02X%02X%02X" % tuple(int(round(min(255, max(0, c)))) for c in x)
    return x[0]


def snap(kind, keys):
    """Rive keys sit on whole frames; AE keys may not (time-stretched or copied from another frame rate). Keys are
    re-placed on the frames around them so that every whole frame shows AE's value exactly: each segment keeps the
    part of its curve between its first and last whole frame (split exactly), a key sitting between two frames
    becomes a one-frame straight join, and a switching (hold) value takes effect on the first frame at or after it."""
    ks = sorted(keys, key=lambda k: k[0])
    if all(abs(k[0] - round(k[0])) < _FRAC for k in ks):
        return ks
    if kind not in ("double", "color"):
        return [(math.ceil(k[0] - _FRAC),) + tuple(k[1:]) for k in ks]

    def value(i, t):
        f, v, interp, ease = ks[i]
        if i == len(ks) - 1 or interp in (None, "hold"):
            return _vec(kind, v)
        g, w = ks[i + 1][0], ks[i + 1][1]
        u = min(1.0, max(0.0, (t - f) / max(1e-9, g - f)))
        a, b = _vec(kind, v), _vec(kind, w)
        if interp == "cubic" and ease:
            u = _bez(0.0, ease[1], ease[3], 1.0, _bez_x(ease[0], ease[2], u))
        elif interp == "cubicValue" and ease:
            return [_bez(a[0], ease[1], ease[3], b[0], _bez_x(ease[0], ease[2], u))]
        return [x + (y - x) * u for x, y in zip(a, b)]

    def sub(i, ta, tb):
        """interpolation of segment i restricted to ta..tb"""
        f, v, interp, ease = ks[i]
        if interp not in ("cubic", "cubicValue") or not ease or tb - ta < _FRAC:
            return interp, None
        g = ks[i + 1][0]
        s0 = _bez_x(ease[0], ease[2], (ta - f) / (g - f))
        s1 = _bez_x(ease[0], ease[2], (tb - f) / (g - f))
        xs = _split((0.0, ease[0], ease[2], 1.0), s0, s1)
        nx = lambda x: (x - xs[0]) / max(1e-12, xs[3] - xs[0])
        if interp == "cubicValue":
            ys = _split((_vec(kind, v)[0], ease[1], ease[3], _vec(kind, ks[i + 1][1])[0]), s0, s1)
            return "cubicValue", (round(nx(xs[1]), 5), ys[1], round(nx(xs[2]), 5), ys[2])
        ys = _split((0.0, ease[1], ease[3], 1.0), s0, s1)
        if abs(ys[3] - ys[0]) < 1e-9:
            return "linear", None
        ny = lambda y: (y - ys[0]) / (ys[3] - ys[0])
        return "cubic", (round(nx(xs[1]), 5), round(ny(ys[1]), 5), round(nx(xs[2]), 5), round(ny(ys[2]), 5))

    out = []
    f0 = ks[0][0]
    if abs(f0 - round(f0)) >= _FRAC:                  # AE holds the first value up to the key
        out.append((math.floor(f0), _unvec(kind, _vec(kind, ks[0][1])), "linear", None))
    for i in range(len(ks) - 1):
        f, g = ks[i][0], ks[i + 1][0]
        interp = ks[i][2]
        a, b = math.ceil(f - _FRAC), math.floor(g + _FRAC)
        if a > b:
            continue
        if interp in (None, "hold"):
            out.append((a, _unvec(kind, value(i, a)), "hold", None))
            continue
        si, se = sub(i, a, b)
        out.append((a, _unvec(kind, value(i, a)), si, se))
        if b > a:
            out.append((b, _unvec(kind, value(i, b)), "linear", None))   # joins the next key across the gap
        elif abs(g - b) >= _FRAC:
            out[-1] = (a, out[-1][1], "linear", None)
    fl, vl = ks[-1][0], ks[-1][1]
    out.append((math.ceil(fl - _FRAC), _unvec(kind, _vec(kind, vl)), "hold", None))
    return out


PRUNED = {"keys": 0}                  # redundant keys removed in this run (reported by the converter)


def _same_val(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(a)))
    return a == b


def prune(kind, keys):
    """Remove the keys that change nothing, the curve staying exactly the same (Rive keys are frame, value, interp):
    a key between two equal values on a flat segment, a repeated hold, a linear key on the line through its
    neighbours, a last key repeating the one before; a constant track keeps one key. Events (callbacks) untouched."""
    if kind == "callback" or len(keys) < 2:
        return keys
    ks = list(keys)
    flat = ("hold", "linear", "cubic")          # between two equal values these draw a constant
    changed = True
    while changed and len(ks) > 1:
        changed = False
        i = 1
        while i < len(ks):
            a, b = ks[i - 1], ks[i]
            c = ks[i + 1] if i + 1 < len(ks) else None
            drop = False
            if a[2] in flat and _same_val(a[1], b[1]):
                if c is None:
                    drop = True                                            # last key repeats the previous one
                elif b[2] == "hold" and a[2] == "hold":
                    drop = True                                            # repeated hold
                elif b[2] in flat and _same_val(b[1], c[1]):
                    drop = True                                            # flat on both sides
            elif (c is not None and kind == "double" and a[2] == "linear" and b[2] == "linear"
                  and c[0] > a[0] and all(isinstance(x[1], (int, float)) for x in (a, b, c))):
                va, vb, vc = float(a[1]), float(b[1]), float(c[1])
                on_line = va + (vc - va) * (b[0] - a[0]) / (c[0] - a[0])
                drop = abs(on_line - vb) <= 1e-6 * max(1.0, abs(vc - va), abs(vb))
            if drop:
                del ks[i]
                PRUNED["keys"] += 1
                changed = True
            else:
                i += 1
    return ks


def dedupe(keys):
    """frames are integers in Rive: two keys rounding to the same frame -> keep the later one."""
    out = []
    for k in sorted(keys, key=lambda k: k[0]):
        f = max(0, int(round(k[0])))
        k = (f,) + tuple(k[1:])
        if out and out[-1][0] == f:
            out[-1] = k
        else:
            out.append(k)
    return out


def keyframe(kind, frame, value, interp, ease):
    if kind == "callback":
        return E("KeyFrameCallback", frame=int(frame))
    tag = {"double": "KeyFrameDouble", "color": "KeyFrameColor", "string": "KeyFrameString", "bool": "KeyFrameBool",
           "id": "KeyFrameId", "uint": "KeyFrameUint"}[kind]
    if kind == "double":
        v = fmt(value)
    elif kind == "color":
        v = value if isinstance(value, str) else argb(value)
    elif kind == "bool":
        v = "true" if value else "false"
    elif kind == "uint":
        v = str(int(round(value)))
    else:
        v = value
    if kind in ("string", "bool", "id", "uint"):
        interp, ease = "hold", None
    kf = E(tag, value=v, frame=int(frame), interpolationType=interp or "hold")
    if interp == "cubic" and ease:
        kf.add(E("CubicEaseInterpolator", x1=float(ease[0]), y1=float(ease[1]), x2=float(ease[2]), y2=float(ease[3])))
    elif interp == "cubicValue" and ease:
        kf.add(E("CubicValueInterpolator", x1=float(ease[0]), y1=float(ease[1]), x2=float(ease[2]), y2=float(ease[3])))
    elif interp in ("cubic", "cubicValue"):
        kf.set("interpolationType", "linear")
    return kf


def document(roots):
    out = ['<Rive version="1" kind="fragment">']
    for r in roots:
        r.write(out, 1)
        out.append("")
    out.append("</Rive>")
    return "\n".join(out) + "\n"
