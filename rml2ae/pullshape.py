"""`ae pull`, shape contents: fill and stroke colours, stroke thickness and paths edited in After Effects go back into
the RML (pull.py does the transforms and calls `Contents.shape` for every Rive Shape it finds in a dumped layer).

How rml2ae built them (convert.emit_shape / _emit_groups) is how they are read back, by rank inside the shape's group:
- one AE path per Rive path child, in order (PointsPath and Triangle -> Path, Rectangle -> Rect, Ellipse -> Ellipse,
  Polygon / Star -> Polystar);
- the paints in reverse order (Rive draws the later paint on top, AE the item higher in the group): the AE fills are
  the Rive fills reversed, the AE strokes the Rive strokes reversed.
When the counts differ (a path or a paint added or deleted in AE), that part of the shape is not pulled: a line says so.

- Colour: `SolidColor.colorValue`, alpha from the AE fill / stroke opacity (not when the paint is hidden by
  isVisible="false": AE shows that as opacity 0). Keyed: `KeyFrameColor`, linear or hold (the build reads colour keys
  so). Gradient fills (a white carrier in AE) are not pulled.
- Thickness: `Stroke.thickness`, static or keyed (eases converted back as for transforms).
- Rectangle / Ellipse: width, height and position (AE centres them: the origin is taken back out).
- PointsPath: the vertices, tangents included (vertex type chosen by the tangents: straight, mirrored, asymmetric,
  detached). Static: the vertex list is rewritten (vertex count may change if no vertex is animated). Keyed in AE:
  one key per AE path key on each vertex property (x, y and the tangents of its type); the vertex count must stay that
  of the RML. AE path keys carry no ease from the build: where AE says linear on a frame the RML eased, the RML's ease
  stays.
- Not pulled: Polygon / Star, Triangle, skinned paths, properties driven by an expression (data binding).
"""
import math

from .model import TYPES, ancestors, argb, is_a
from .pull import EPS, _value, fmt, fold_baked, keys_to_rive, same_keys, same_motion

PATH_TAGS = ("PointsPath", "Rectangle", "Ellipse", "Polygon", "Star", "Triangle")
AE_PATHS = ("path", "rect", "ellipse", "star")
VERTEX_TAGS = ("StraightVertex", "CubicMirroredVertex", "CubicAsymmetricVertex", "CubicDetachedVertex")
VERTEX_PROPS = {"StraightVertex": ("x", "y"),
                "CubicMirroredVertex": ("x", "y", "rotation", "distance"),
                "CubicAsymmetricVertex": ("x", "y", "rotation", "inDistance", "outDistance"),
                "CubicDetachedVertex": ("x", "y", "inRotation", "inDistance", "outRotation", "outDistance")}
GEOM = {"x", "y", "rotation", "distance", "inDistance", "outDistance", "inRotation", "outRotation", "radius"}
PEPS = 0.01                     # px: path coordinates


def pkey(tag, name):
    """property key of `name` on an element of type `tag` (the most derived owner)"""
    for t in ancestors(tag):
        for p in (TYPES.get(t) or {}).get("properties", []):
            if p["name"] == name and p["owner"] == t:
                return p["key"]
    raise KeyError(f"{tag}.{name}")


def hexc(r, g, b, a):
    """(r, g, b, a) in 0..1 -> 'AARRGGBB'"""
    q = [max(0, min(255, int(round(c * 255)))) for c in (a, r, g, b)]
    return "%02X%02X%02X%02X" % tuple(q)


def color_key_xml(frame, v, interp, _ease=None):
    return f'<KeyFrameColor value="{v}" frame="{int(round(frame))}" interpolationType="{"hold" if interp == "hold" else "linear"}"/>'


def ae_at(ks, t):
    """value of AE keys at time t (hold or straight line between keys: the times we read are the keys' own)"""
    if t <= ks[0]["t"]:
        return ks[0]["v"]
    for k0, k1 in zip(ks, ks[1:]):
        if k0["t"] <= t < k1["t"]:
            if k0["o"] == "h":
                return k0["v"]
            u = (t - k0["t"]) / (k1["t"] - k0["t"])
            if isinstance(k0["v"], list):
                return [a + (b - a) * u for a, b in zip(k0["v"], k1["v"])]
            return k0["v"] + (k1["v"] - k0["v"]) * u
    return ks[-1]["v"]


# ---- vertices: AE (vertex, in tangent, out tangent) <-> Rive vertex types
def _zero(p):
    return math.hypot(p[0], p[1]) < 1e-6


def _opposite(i, o):
    """in and out tangents on one line, on either side of the vertex"""
    di, do = math.hypot(*i), math.hypot(*o)
    return abs(i[0] * o[1] - i[1] * o[0]) <= 1e-3 * di * do and i[0] * o[0] + i[1] * o[1] < 0


def fits(tag, i, o):
    """can a vertex of type `tag` hold these tangents?"""
    if tag == "StraightVertex":
        return _zero(i) and _zero(o)
    if tag == "CubicMirroredVertex":
        return (_zero(i) and _zero(o)) or (_opposite(i, o) and abs(math.hypot(*i) - math.hypot(*o)) < PEPS)
    if tag == "CubicAsymmetricVertex":
        return _zero(i) or _zero(o) or _opposite(i, o)
    return tag == "CubicDetachedVertex"


def vertex_type(i, o):
    for tag in VERTEX_TAGS:
        if fits(tag, i, o):
            return tag
    return "CubicDetachedVertex"


def vertex_values(tag, v, i, o, prev=None):
    """{property: value} of a vertex of type `tag` at AE vertex v with tangents i, o (`prev`: the rotations of the
    previous key, so an angle never jumps by a full turn between two keys)"""
    di, do = math.hypot(*i), math.hypot(*o)

    def ang(p, q=None, name=None):
        if _zero(p) and q is not None and not _zero(q):
            a = math.atan2(-q[1], -q[0])
        elif _zero(p):
            a = (prev or {}).get(name, 0.0)
        else:
            a = math.atan2(p[1], p[0])
        if prev and name in prev:
            a += 2 * math.pi * round((prev[name] - a) / (2 * math.pi))
        return a
    out = {"x": v[0], "y": v[1]}
    if tag == "CubicMirroredVertex":
        out.update(rotation=ang(o, i, "rotation"), distance=(di + do) / 2)
    elif tag == "CubicAsymmetricVertex":
        out.update(rotation=ang(o, i, "rotation"), inDistance=di, outDistance=do)
    elif tag == "CubicDetachedVertex":
        out.update(inRotation=ang(i, None, "inRotation"), inDistance=di, outRotation=ang(o, None, "outRotation"), outDistance=do)
    return out


def tangents(tag, get):
    """(in, out) tangents of a vertex of type `tag`, its values read by get(name, default)"""
    if tag == "CubicDetachedVertex":
        ir, idist, orr, odist = get("inRotation"), get("inDistance"), get("outRotation"), get("outDistance")
        return [math.cos(ir) * idist, math.sin(ir) * idist], [math.cos(orr) * odist, math.sin(orr) * odist]
    if tag == "CubicMirroredVertex":
        r, d = get("rotation"), get("distance")
        return [-math.cos(r) * d, -math.sin(r) * d], [math.cos(r) * d, math.sin(r) * d]
    if tag == "CubicAsymmetricVertex":
        r, di, do = get("rotation"), get("inDistance"), get("outDistance")
        return [-math.cos(r) * di, -math.sin(r) * di], [math.cos(r) * do, math.sin(r) * do]
    return [0.0, 0.0], [0.0, 0.0]


def ae_shape(s):
    return s["vs"], s["in"], s["out"]


def same_shape(a, b):
    if len(a[0]) != len(b[0]):
        return False
    return all(abs(p[0] - q[0]) <= PEPS and abs(p[1] - q[1]) <= PEPS for la, lb in zip(a, b) for p, q in zip(la, lb))


class Contents:
    """Pulls the contents of Rive Shapes for one comp (one animation) into the RML texts."""

    def __init__(self, ab, anim, anims, file_of, new_id, changes, pulled_ids, dry):
        self.ab, self.anim, self.anims, self.file_of, self.new_id = ab, anim, anims, file_of, new_id
        self.changes, self.pulled, self.dry = changes, pulled_ids, dry
        self.window = False
        self.static_ok = False
        self.f = None

    # ---- helpers
    def note(self, el, text, shape=None):
        shape = shape or el
        self.changes.append(f"{self.ab.name} / {self.anim.name}: {shape.name} ({shape.id}) {text}")
        self.pulled.add(shape.id)

    def static_note(self, el, text, shape=None):
        shape = shape or el
        self.changes.append(f"{self.ab.name}: {shape.name} ({shape.id}) {text}")
        self.pulled.add(shape.id)

    def ensure_id(self, el):
        """an element must have an id to be keyed: one is given in the text (and the model)"""
        if el.id:
            return el.id
        nid = self.new_id()
        if not self.dry:
            self.f.set_attr_el(el, "id", nid)
        el.attrs["id"] = nid
        el.id = nid
        return nid

    def frame(self, t):
        return t * self.anim.fps * self.anim.speed

    def rml_keys(self, el, pk):
        return self.anim.keys.get((el.id, pk)) if el.id else None

    def set_keys(self, el, pk, keys, kxml=None):
        oid = self.ensure_id(el)
        if not self.dry:
            self.f.set_keys(self.anim.id, oid, pk, keys, kxml)
        self.anim.keys[(oid, pk)] = list(keys)

    def set_attr(self, el, name, value):
        if not self.dry:
            self.f.set_attr_el(el, name, value)
        elif value is None:
            el.attrs.pop(name, None)
        else:
            el.attrs[name] = value

    def double(self, el, name, rec, dim, unit=1.0, default=0.0, shape=None, label=None, shift=0.0):
        """one numeric property (as pull.apply does for transforms): keys, keys removed, static value"""
        if rec is None or rec.get("x"):
            return
        label = label or name
        pk = pkey(el.tag, name)
        ks = rec.get("k") or []
        rml = self.rml_keys(el, pk)
        eps = 0.01 if unit == 1.0 else EPS.get(name, 0.002)
        if shift:
            ks = [dict(k, v=(k["v"][:dim] + [k["v"][dim] - shift] + k["v"][dim + 1:]) if dim is not None else k["v"] - shift) for k in ks]
        if len(ks) >= 2 or (ks and rml):
            new = keys_to_rive(ks, dim, self.anim.fps, self.anim.speed, unit)
            if self.window and rml and rml[-1][0] >= self.anim.duration - 0.01 and (not new or new[-1][0] < self.anim.duration - 0.01):
                new.append(rml[-1])
            old = list(rml or [])
            if not same_keys(new, old, eps) and not same_motion(new, old, eps, el.num(name, default)):
                new = fold_baked(new, (getattr(self.anim, "raw_keys", None) or {}).get((el.id, pk)) or [], eps)
                self.set_keys(el, pk, new)
                self.note(el, f"{label}: {len(new)} key(s) {'replaced' if old else 'added'}", shape)
            return
        cur = ((rec["v"][dim] if dim is not None else rec["v"]) - shift) * unit
        if not ks and rml and self.static_ok and not same_motion([(0, cur, "hold", None)], rml, eps):
            self.set_keys(el, pk, [])
            self.note(el, f"{label}: keys removed", shape)
        if not ks and self.static_ok:
            was = el.num(name, default)
            if abs(cur - was) > eps:
                self.set_attr(el, name, fmt(cur) if abs(cur - default) > 1e-9 or el.get(name) is not None else None)
                self.static_note(el, f"{label} {fmt(was)} -> {fmt(cur)}", shape)

    # ---- one Rive Shape and the items of its AE group
    def shape(self, shape, items, dedicated, static_ok, window):
        self.f = self.file_of(shape.id)
        if self.f is None:
            return
        self.static_ok, self.window = static_ok, window
        kids = shape.children
        paths = [c for c in kids if c.tag in PATH_TAGS]
        ae_paths = [it for it in items if it["m"] in AE_PATHS]
        if len(paths) == len(ae_paths):
            for p, it in zip(paths, ae_paths):
                self.path(shape, p, it)
        elif ae_paths or paths:
            self.note(shape, f"paths not pulled: {len(ae_paths)} in AE, {len(paths)} in the RML")
        fills = [c for c in kids if c.tag == "Fill"
                 and not (dedicated and c.find("Feather") is not None and not self.gradient(c))]
        strokes = [c for c in kids if c.tag == "Stroke"]
        for kind, rive, label in (("fill", fills, "fill"), ("stroke", strokes, "stroke")):
            ae = [it for it in items if it["m"] == kind]
            if len(ae) != len(rive):
                self.note(shape, f"{label}s not pulled: {len(ae)} in AE, {len(rive)} in the RML")
                continue
            for paint, it in zip(reversed(rive), ae):
                n = sum(1 for c in rive if c is not paint and rive.index(c) < rive.index(paint))
                lab = label + (f" {n + 1}" if len(rive) > 1 else "")
                self.paint(shape, paint, it, lab)
                if kind == "stroke":
                    self.double(paint, "thickness", it.get("w"), None, default=1.0, shape=shape, label=lab + " thickness")

    @staticmethod
    def gradient(paint):
        return paint.find("LinearGradient") is not None or paint.find("RadialGradient") is not None

    def paint(self, shape, paint, it, label):
        sc = paint.find("SolidColor")
        if sc is None:
            return                                  # gradient: AE holds a white carrier, not the colours
        c, o = it.get("c"), it.get("o")
        if c is None or c.get("x"):
            return
        use_alpha = paint.get("isVisible") != "false" and o is not None and not o.get("x")
        r0, g0, b0, a0 = argb(sc.get("colorValue"))
        ck = c.get("k") or []
        ok = (o.get("k") or []) if use_alpha else []
        pk = pkey("SolidColor", "colorValue")
        rml = self.rml_keys(sc, pk)

        def at(t):
            rgb = ae_at(ck, t) if ck else c["v"]
            a = (ae_at(ok, t) if ok else o["v"]) / 100.0 if use_alpha else a0
            return hexc(rgb[0], rgb[1], rgb[2], a)
        old = [(f, hexc(*v), "hold" if i == "hold" else "linear", None) for f, v, i, _e in (rml or [])]

        def norm(lst):                              # the last key's interpolation leads nowhere
            return [(int(round(f)), v, i if n < len(lst) - 1 else "") for n, (f, v, i, _e) in enumerate(lst)]
        if len(ck) >= 2 or len(ok) >= 2 or ((ck or ok) and rml):
            times = []
            for k in sorted(ck + ok, key=lambda k: k["t"]):
                if not times or k["t"] - times[-1] > 1e-4:
                    times.append(k["t"])
            byt = {}
            for k in ok + ck:                       # a colour key's interpolation wins over an opacity key's
                byt[round(k["t"], 4)] = k["o"]
            new = [(self.frame(t), at(t), "hold" if byt.get(round(t, 4)) == "h" else "linear", None) for t in times]
            if self.window and old and old[-1][0] >= self.anim.duration - 0.01 and new[-1][0] < self.anim.duration - 0.01:
                new.append(old[-1])
            if norm(new) != norm(old):
                self.set_keys(sc, pk, new, color_key_xml)
                self.note(shape, f"{label} colour: {len(new)} key(s) {'replaced' if old else 'added'}")
            return
        cur = at(0)
        if rml and self.static_ok and any(v != cur for _f, v, _i, _e in old):
            self.set_keys(sc, pk, [])
            self.note(shape, f"{label} colour: keys removed")
        if self.static_ok:
            was = hexc(r0, g0, b0, a0)
            if cur != was:
                self.set_attr(sc, "colorValue", cur)
                self.static_note(shape, f"{label} colour {was} -> {cur}")

    # ---- paths
    def path(self, shape, p, it):
        if p.tag in ("Rectangle", "Ellipse") and it["m"] in ("rect", "ellipse"):
            size, pos = it.get("size"), it.get("pos")
            self.double(p, "width", size, 0, shape=shape, label=f"{p.name} width")
            self.double(p, "height", size, 1, shape=shape, label=f"{p.name} height")
            if pos is None or (size or {}).get("k") or pos.get("x"):
                return                              # the centre moves with the size: position read only at a fixed size
            w, h = p.num("width"), p.num("height")
            dx, dy = (0.5 - p.num("originX", 0.5)) * w, (0.5 - p.num("originY", 0.5)) * h
            self.double(p, "x", pos, 0, shape=shape, label=f"{p.name} x", shift=dx)
            self.double(p, "y", pos, 1, shape=shape, label=f"{p.name} y", shift=dy)
        elif p.tag == "PointsPath" and it["m"] == "path":
            self.points(shape, p, it["p"])

    def rml_points(self, pp, frame=None):
        """(vertices, ins, outs) of a PointsPath as the build drew it (path offset in, radii baked), at `frame`"""
        from .convert import round_corners
        ox, oy = pp.num("x"), pp.num("y")
        V, I, O, R = [], [], [], []
        for v in pp.children:
            if not is_a(v.tag, "Vertex"):
                continue

            def get(name, default=0.0, v=v):
                k = self.anim.keys.get((v.id, pkey(v.tag, name))) if (v.id and frame is not None) else None
                return _value(k, frame) if k else v.num(name, default)
            V.append([get("x") + ox, get("y") + oy])
            i, o = tangents(v.tag, get)
            I.append(i)
            O.append(o)
            R.append(get("radius") if v.tag == "StraightVertex" else 0.0)
        if any(r > 0 for r in R):
            V, I, O = round_corners(V, I, O, R, pp.get("isClosed", "true") == "true")
        return V, I, O

    def vertex_keys(self, pp):
        """{(vertex, property key): key list} of this animation for the path's vertices"""
        out = {}
        for v in pp.children:
            if v.id and is_a(v.tag, "Vertex"):
                for (oid, pk), ks in self.anim.keys.items():
                    if oid == v.id:
                        out[(v, pk)] = ks
        return out

    def points(self, shape, pp, rec):
        if rec.get("x") or any(not is_a(c.tag, "Vertex") or c.children for c in pp.children):
            return                                  # driven, or skinned / weighted vertices
        verts = list(pp.children)
        ks = rec.get("k") or []
        vk = self.vertex_keys(pp)
        ox, oy = pp.num("x"), pp.num("y")
        if len(ks) >= 2 or (ks and vk):
            self.points_keyed(shape, pp, verts, ks, vk, ox, oy)
            return
        cur = ae_shape(rec["v"])
        if vk and self.static_ok:
            frames = sorted({k[0] for lst in vk.values() for k in lst})
            if not all(same_shape(cur, self.rml_points(pp, f)) for f in frames):
                for (v, pk) in vk:
                    self.set_keys(v, pk, [])
                self.note(shape, f"{pp.name}: path keys removed")
        if not self.static_ok or same_shape(cur, self.rml_points(pp)):
            return
        animated = any(o == v.id for v in verts if v.id for a in self.anims for (o, _pk) in a.keys)
        V, I, O = cur
        if len(V) == len(verts):
            tags = [v.tag if fits(v.tag, i, o) else vertex_type(i, o) for v, i, o in zip(verts, I, O)]
            if animated and any(t != v.tag for t, v in zip(tags, verts)):
                self.static_note(shape, f"{pp.name}: path not pulled (an animated vertex would change type)")
                return
            olds = verts
        else:
            if animated:
                self.static_note(shape, f"{pp.name}: path not pulled ({len(V)} vertices in AE, {len(verts)} in the RML, "
                                        "and some are animated)")
                return
            tags = [vertex_type(i, o) for i, o in zip(I, O)]
            olds = [None] * len(V)
        xml = []
        for tag, old, v, i, o in zip(tags, olds, V, I, O):
            vals = vertex_values(tag, [v[0] - ox, v[1] - oy], i, o)
            attrs = {k: a for k, a in (old.attrs.items() if old is not None else []) if k not in GEOM}
            body = " ".join(f'{k}="{fmt(x)}"' for k, x in vals.items())
            rest = "".join(f' {k}="{a}"' for k, a in attrs.items())
            xml.append(f"<{tag} {body}{rest}/>")
        if not self.dry:
            self.f.replace_children(pp, "".join(xml))
        self.static_note(shape, f"{pp.name}: path of {len(verts)} -> {len(V)} vertices rewritten")

    def points_keyed(self, shape, pp, verts, ks, vk, ox, oy):
        n = len(verts)
        if any(len(k["v"]["vs"]) != n for k in ks):
            self.note(shape, f"{pp.name}: path keys not pulled (the vertex count differs from the RML's {n})")
            return
        frames = [self.frame(k["t"]) for k in ks]
        rml_frames = sorted({k[0] for lst in vk.values() for k in lst})
        if [int(round(f)) for f in frames] == [int(round(f)) for f in rml_frames] and \
                all(same_shape(ae_shape(k["v"]), self.rml_points(pp, f)) for k, f in zip(ks, frames)):
            return                                  # the build's own keys: eases aside (AE path keys carry none)
        for j, v in enumerate(verts):
            if not all(fits(v.tag, k["v"]["in"][j], k["v"]["out"][j]) for k in ks):
                self.note(shape, f"{pp.name}: path keys not pulled (vertex {j + 1} would need another type than {v.tag})")
                return
        # interpolation per AE key: hold, linear, or the influence of a bezier ease (speed not used: shape units)
        interp = []
        for a, (k, nk) in enumerate(zip(ks, ks[1:] + [None])):
            if nk is None:
                interp.append(("linear", None))
            elif k["o"] == "h":
                interp.append(("hold", None))
            elif k["o"] == "l" and nk["i"] == "l":
                interp.append(("linear", None))
            else:
                x1 = k["oe"][0][1] / 100.0 if k["oe"] else 0.0
                x2 = 1 - (nk["ie"][0][1] / 100.0 if nk["ie"] else 0.0)
                interp.append(("cubic", (round(x1, 4), 0.0, round(x2, 4), 1.0)) if abs(x1) + abs(1 - x2) > 0.02 else ("linear", None))
        written = 0
        for j, v in enumerate(verts):
            prev = None
            rows = {name: [] for name in VERTEX_PROPS[v.tag]}
            for k, f, (ip, ease) in zip(ks, frames, interp):
                vals = vertex_values(v.tag, [k["v"]["vs"][j][0] - ox, k["v"]["vs"][j][1] - oy], k["v"]["in"][j], k["v"]["out"][j], prev)
                prev = vals
                for name in rows:
                    rows[name].append((f, vals[name], ip, ease))
            for name, new in rows.items():
                pk = pkey(v.tag, name)
                old = self.anim.keys.get((v.id, pk)) if v.id else None
                if old:
                    by_f = {int(round(o[0])): o for o in old}
                    new = [(f, x, o[2], o[3]) if (ip == "linear" and o is not None and o[2] == "cubic") else (f, x, ip, e)
                           for f, x, ip, e in new for o in [by_f.get(int(round(f)))]]
                    if self.window and old[-1][0] >= self.anim.duration - 0.01 and new[-1][0] < self.anim.duration - 0.01:
                        new.append(old[-1])
                eps = 0.0005 if "Rotation" in name or name == "rotation" else PEPS
                if old and same_keys(new, old, eps):
                    continue
                if not old and all(abs(x[1] - v.num(name)) <= eps for x in new):
                    continue                        # constant at its static value: no key needed
                self.set_keys(v, pk, new)
                written += 1
        if written:
            self.note(shape, f"{pp.name}: path keys -> {written} vertex propert{'y' if written == 1 else 'ies'} keyed ({len(ks)} keys)")
