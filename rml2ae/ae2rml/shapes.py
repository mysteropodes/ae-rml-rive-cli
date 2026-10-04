"""AE shape-layer contents -> Rive shapes.

AE semantics reproduced (lottie-web / AE behaviour):
  * a paint (Fill, Stroke, Gradient Fill/Stroke) draws every path ABOVE it in its group, including the paths of
    the groups above it (through their transforms);
  * a path operation (Trim Paths, Round Corners) changes every path above it, so it also shows in the paints above;
  * items higher in the list draw on top (Rive: first child on top; inside one Shape the LAST paint is on top);
  * a Repeater copies everything above it in its group; each copy is transformed k times by its step.
"""
import math

from . import geom
from .aexpr import ShapeValue, tonum, unwrap
from .keys import AProp, progress_keys
from .rml import E
from .util import argb, clean

PATHS = {"ADBE Vector Shape - Rect": "rect", "ADBE Vector Shape - Ellipse": "ellipse",
         "ADBE Vector Shape - Star": "star", "ADBE Vector Shape - Group": "path"}
PAINTS = {"ADBE Vector Graphic - Fill": "fill", "ADBE Vector Graphic - Stroke": "stroke",
          "ADBE Vector Graphic - G-Fill": "gfill", "ADBE Vector Graphic - G-Stroke": "gstroke"}
MODS = {"ADBE Vector Filter - Trim": "trim", "ADBE Vector Filter - Repeater": "repeater",
        "ADBE Vector Filter - RC": "round", "ADBE Vector Filter - Merge": "merge",
        "ADBE Vector Filter - Offset": "offset", "ADBE Vector Filter - PB": "pucker",
        "ADBE Vector Filter - Twist": "twist", "ADBE Vector Filter - Roughen": "wiggle paths",   # AE 26: Wiggle Paths
        "ADBE Vector Filter - Wiggler": "wiggle transform", "ADBE Vector Filter - Zigzag": "zig zag",
        "ADBE Vector Filter - Wiggle Transform": "wiggle transform"}
# AE path operations drawn by a Luau ScriptedPathEffect (ae2rml/luau), nested in each paint that draws the path
PATH_FX = {"zig zag": "ae_zigzag", "offset": "ae_offset", "pucker": "ae_pucker", "twist": "ae_twist",
           "roughen": "ae_roughen", "wiggle paths": "ae_roughen"}
BLEND = {1: "srcOver", 2: "srcOver", 3: "darken", 4: "multiply", 5: "colorBurn", 6: "colorBurn", 7: "darken",
         8: "srcOver", 9: "lighten", 10: "screen", 11: "colorDodge", 12: "additive", 13: "lighten", 14: "srcOver",
         15: "overlay", 16: "softLight", 17: "hardLight", 18: "hardLight", 19: "hardLight", 20: "hardLight",
         21: "hardLight", 22: "difference", 23: "exclusion", 24: "difference", 25: "srcOver", 26: "hue",
         27: "saturation", 28: "color", 29: "luminosity"}


class Item:
    __slots__ = ("kind", "mn", "name", "prop", "children", "idx")

    def __init__(self, kind, mn, name, prop, idx, children=None):
        self.kind, self.mn, self.name, self.prop, self.idx = kind, mn, name, prop, idx
        self.children = children or []

    def sub(self, mn):
        try:
            return self.prop.property(mn)
        except Exception:
            return None


def _kids(g):
    try:
        return list(g.properties)
    except Exception:
        return []


def _clockwise(el):
    """is a PointsPath's first-frame outline clockwise on screen (y down)? Shoelace over its vertices"""
    pts = [(float(v.attrs.get("x", 0)), float(v.attrs.get("y", 0))) for v in el.children if v.tag.endswith("Vertex")]
    if len(pts) < 3:
        return True
    a = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
    return a >= 0


def _enabled(p):
    try:
        return bool(p.enabled)
    except Exception:
        return True


def _stored(p):
    """a property really present in the .aep (py-aep also hands back synthesized defaults, e.g. unused dash slots)"""
    if not hasattr(p, "_composition") and not hasattr(p, "_tdum"):
        return True
    return getattr(p, "_composition", None) is not None or getattr(p, "_tdum", None) is not None


def read_items(group):
    """ADBE Root Vectors Group / ADBE Vectors Group -> [Item]"""
    out = []
    for i, p in enumerate(_kids(group)):
        mn = getattr(p, "match_name", "")
        if not _enabled(p):
            continue
        name = clean(getattr(p, "name", ""))
        if mn == "ADBE Vector Group":
            try:
                inner = p.property("ADBE Vectors Group")
            except KeyError:                  # an empty group: py-aep raises instead of returning None (Mh_Safety)
                inner = None
            out.append(Item("group", mn, name, p, i, read_items(inner) if inner is not None else []))
        elif mn in PATHS:
            out.append(Item(PATHS[mn], mn, name, p, i))
        elif mn in PAINTS:
            out.append(Item(PAINTS[mn], mn, name, p, i))
        elif mn in MODS:
            out.append(Item(MODS[mn], mn, name, p, i))
        elif mn.startswith("ADBE Vector"):
            out.append(Item("other", mn, name, p, i))
    return out


class PathRef:
    __slots__ = ("item", "groups", "trims", "round", "reps", "key", "merge", "mods")

    def __init__(self, item, groups, key):
        self.item, self.groups, self.key = item, groups, key
        self.mods = []                  # PATH_FX operations, in AE order
        self.trims = []
        self.round = None
        self.reps = []
        self.merge = None               # (Merge Paths item, mode, rank of this path in the merge)


def process(items, groups, key, lb):
    """-> (entries, paths). entries: ('paint', item, groups, [(PathRef, reps_before)]) | ('group', item, entries, key)
    | ('repeat', item, entries, key)"""
    out, paths = [], []
    for it in items:
        k = it.kind
        ik = f"{key}.{it.idx}"
        if k in ("rect", "ellipse", "star", "path"):
            paths.append(PathRef(it, groups, ik))
        elif k == "group":
            sub, sub_paths = process(it.children, groups + (it,), ik, lb)
            if sub:
                out.append(("group", it, sub, ik))
            paths.extend(sub_paths)
        elif k in ("fill", "stroke", "gfill", "gstroke"):
            out.append(("paint", it, groups, [(pr, len(pr.reps)) for pr in paths], ik))
        elif k == "trim":
            for pr in paths:
                pr.trims.append(it)
        elif k == "round":
            for pr in paths:
                pr.round = it
        elif k == "repeater":
            out = [("repeat", it, out, ik)]
            for pr in paths:
                pr.reps.append((it, ik))
        elif k == "merge":
            mp = it.sub("ADBE Vector Merge Type")
            try:
                mode = int(tonum(mp.value)) if mp is not None else 1
            except Exception:
                mode = 1
            for rank, pr in enumerate(paths):
                if pr.merge is None:
                    pr.merge = (it, mode, rank)
            # AE ignores the paints above a Merge Paths (it adds a Fill below it for that reason): only the merged
            # path is drawn — the stock's Simple_Elements 6 preview is a hollow star, not two filled ones
            stripped = _strip_paints(out)
            if stripped != out:
                lb.note("converted", it.name, "paints above the Merge Paths dropped, as AE does")
            out = stripped
        elif k in PATH_FX:
            if any(pr.trims for pr in paths):
                lb.note("approx", it.name, f"Trim Paths above the {k}: Rive trims after the path effect")
            if any(pr.round is not None for pr in paths):
                lb.note("approx", it.name, f"Round Corners above the {k}: applied to the path first, as in AE")
            for pr in paths:
                pr.mods.append(it)
        else:
            lb.note("unsupported", it.name, f"shape operation '{k}' ({it.mn}) is not in Rive: ignored")
    return out, paths


def _strip_paints(entries):
    out = []
    for e in entries:
        if e[0] == "paint":
            continue
        if e[0] in ("group", "repeat"):
            sub = _strip_paints(e[2])
            if sub:
                out.append((e[0], e[1], sub, e[3]))
            continue
        out.append(e)
    return out


# ====================================================================== emission
class ShapeBuilder:
    """Emits one shape layer's contents into a container element."""

    def __init__(self, lb):
        self.lb = lb                   # the LayerBuild (ids, anim, timeline, report, colour registry)
        self.tl = lb.tl

    def prop(self, p, what):
        return AProp(self.tl, p, self.lb.L, what=f"{self.lb.name} · {what}", stretch=self.lb.stretch)

    def build(self, container, root_group, blend=None):
        items = read_items(root_group)
        entries, _paths = process(items, (), "c", self.lb)
        self.emit(entries, container, (), blend)

    # ---------------------------------------------------------------- entries
    def emit(self, entries, container, groups, blend):
        pending = []                     # consecutive paints that may share one Shape
        for e in entries:
            if e[0] == "paint":
                pending.append(e)
                continue
            self.flush(pending, container, blend)
            pending = []
            if e[0] == "group":
                self.emit_group(e, container, blend)
            elif e[0] == "repeat":
                self.emit_repeat(e, container, groups, blend)
        self.flush(pending, container, blend)

    def flush(self, paints, container, blend):
        """consecutive paints over the same paths and without animated opacity share one Shape"""
        i = 0
        while i < len(paints):
            j = i + 1
            while (j < len(paints) and self._same_paths(paints[i], paints[j]) and not self._own_opacity(paints[i])
                   and not self._own_opacity(paints[j])):
                j += 1
            self.emit_shape(paints[i:j], container, blend)
            i = j

    @staticmethod
    def _same_paths(a, b):
        return len(a[3]) == len(b[3]) and all(x[0] is y[0] and x[1] == y[1] for x, y in zip(a[3], b[3]))

    def _own_opacity(self, e):
        it = e[1]
        mn = "ADBE Vector Fill Opacity" if it.kind in ("fill", "gfill") else "ADBE Vector Stroke Opacity"
        p = it.sub(mn)
        return p is not None and self.prop(p, it.name + " opacity").animated

    def emit_group(self, e, container, blend):
        _, g, sub, key = e
        lb = self.lb
        gb = blend
        bm = g.sub("ADBE Vector Blend Mode")
        if bm is not None:
            try:
                v = int(tonum(bm.value))
                if v > 1:
                    gb = BLEND.get(v, "srcOver")
            except Exception:
                pass
        node, inner = self.transform_node(g, key)
        if node is None:
            return
        container.add(node)
        self.emit(sub, inner, (), gb)

    def transform_node(self, g, key):
        """a shape group's transform -> Node (+ anchor Node)"""
        lb = self.lb
        tr = g.sub("ADBE Vector Transform Group")
        node = E("Node", id=lb.id(key, "g"), name=g.name)
        if tr is None:
            return node, node
        pos = self.prop(tr.property("ADBE Vector Position"), g.name + " position")
        anc = self.prop(tr.property("ADBE Vector Anchor"), g.name + " anchor")
        sc = self.prop(tr.property("ADBE Vector Scale"), g.name + " scale")
        rot = self.prop(tr.property("ADBE Vector Rotation"), g.name + " rotation")
        op = self.prop(tr.property("ADBE Vector Group Opacity"), g.name + " opacity")
        sk = tr.property("ADBE Vector Skew")
        skp = self.prop(sk, g.name + " skew") if sk is not None else None
        if skp is not None and (skp.animated or abs(tonum(skp.static(None, 0))) > 1e-6):
            return self._skewed_node(g, key, node, pos, anc, op)
        lb.put(node, "x", pos, 0)
        lb.put(node, "y", pos, 1)
        lb.put(node, "rotation", rot, None, math.pi / 180)
        lb.put(node, "scaleX", sc, 0, 0.01, default=1.0)
        lb.put(node, "scaleY", sc, 1, 0.01, default=1.0)
        lb.put(node, "opacity", op, None, 0.01, default=1.0)
        if anc.animated or abs(anc.static(0)) > 1e-9 or abs(anc.static(1)) > 1e-9:
            inner = E("Node", id=lb.id(key, "ga"), name=g.name + " · anchor")
            lb.put(inner, "x", anc, 0, -1.0)
            lb.put(inner, "y", anc, 1, -1.0)
            node.add(inner)
            return node, inner
        return node, node

    def _skewed_node(self, g, key, node, pos, anc, op):
        """a skewed group (Rive nodes have no skew): its linear part R(rot)·Skew·S = R(phi)·diag(sx, sy)·R(theta),
        exact, as a node (position, phi, sx, sy) holding a node (theta) holding the anchor offset — keyed per frame
        when anything moves"""
        lb = self.lb
        inner = E("Node", id=lb.id(key, "gsk"), name=g.name + " · skew")
        node.add(inner)
        anode = E("Node", id=lb.id(key, "ga"), name=g.name + " · anchor")
        inner.add(anode)
        rows = {k: [] for k in ("x", "y", "phi", "sx", "sy", "th", "ax", "ay")}
        prev = None
        for f in range(self.tl.nframes + 1):
            t = f / self.tl.fps
            m = self._group_matrix(g, t)
            phi, sx, sy, th = geom.rsr(m)
            if prev is not None:
                # keep the angles continuous (a half-turn of both angles with both scales negated is the same matrix)
                phi, th = geom.unwrap(prev[0], phi), geom.unwrap(prev[1], th)
            prev = (phi, th)
            pv = unwrap(pos.at(t)) if pos.animated else pos.static(None, [0, 0])
            av = unwrap(anc.at(t)) if anc.animated else anc.static(None, [0, 0])
            for k, v in (("x", tonum(pv[0])), ("y", tonum(pv[1])), ("phi", phi), ("sx", sx), ("sy", sy), ("th", th),
                         ("ax", -tonum(av[0])), ("ay", -tonum(av[1]))):
                rows[k].append((f, v))
        lb.put_dense(node, "x", rows["x"])
        lb.put_dense(node, "y", rows["y"])
        lb.put_dense(node, "rotation", rows["phi"])
        lb.put_dense(node, "scaleX", rows["sx"])
        lb.put_dense(node, "scaleY", rows["sy"])
        lb.put_dense(inner, "rotation", rows["th"])
        lb.put_dense(anode, "x", rows["ax"])
        lb.put_dense(anode, "y", rows["ay"])
        lb.put(node, "opacity", op, None, 0.01, default=1.0)
        lb.note("converted", g.name, "group skew → rotation · scale · rotation on two nodes (exact)")
        return node, anode

    # ---------------------------------------------------------------- repeater
    def emit_repeat(self, e, container, groups, blend):
        _, r, sub, key = e
        lb = self.lb
        cp = self.prop(r.sub("ADBE Vector Repeater Copies"), r.name + " copies")
        off = self.prop(r.sub("ADBE Vector Repeater Offset"), r.name + " offset")
        order = r.sub("ADBE Vector Repeater Order")
        tr = r.sub("ADBE Vector Repeater Transform")
        T = {n: self.prop(tr.property(mn), r.name + " " + n) if tr is not None else None for n, mn in (
            ("anchor", "ADBE Vector Repeater Anchor"), ("position", "ADBE Vector Repeater Position"),
            ("scale", "ADBE Vector Repeater Scale"), ("rotation", "ADBE Vector Repeater Rotation"),
            ("op1", "ADBE Vector Repeater Opacity 1"), ("op2", "ADBE Vector Repeater Opacity 2"))}
        n_max = int(math.ceil(max([cp.static(None, 3)] + ([tonum(unwrap(cp.at(f / self.tl.fps))) for f in range(self.tl.nframes + 1)]
                                                            if cp.animated else []))))
        n_max = max(0, min(n_max, 500))
        if n_max == 0:
            return
        animated = any(p is not None and p.animated for p in T.values()) or off.animated
        above = order is not None and int(tonum(order.value)) == 2
        ks = list(range(n_max))
        if above:
            ks.reverse()
        wrap = E("Node", id=lb.id(key, "rep"), name=r.name)
        container.add(wrap)
        for k in ks:
            node = E("Node", id=lb.id(key, "rep", k), name=f"{r.name} {k + 1}")
            wrap.add(node)
            if not animated:
                m, o = self.repeat_matrix(T, off.static(None), k, n_max, 0.0)
                x, y, rot, sx, sy, _sk = geom.decompose(m)
                node.set("x", x).set("y", y).set("rotation", rot).set("scaleX", sx).set("scaleY", sy)
                if abs(o - 1) > 1e-6:
                    node.set("opacity", o)
            else:
                dense = {n: [] for n in ("x", "y", "rotation", "scaleX", "scaleY", "opacity")}
                prev_rot = None
                for f in range(self.tl.nframes + 1):
                    t = f / self.tl.fps
                    m, o = self.repeat_matrix(T, tonum(unwrap(off.at(t))), k, n_max, t)
                    x, y, rot, sx, sy, _sk = geom.decompose(m)
                    rot = geom.unwrap(prev_rot, rot)
                    prev_rot = rot
                    for nme, v in (("x", x), ("y", y), ("rotation", rot), ("scaleX", sx), ("scaleY", sy), ("opacity", o)):
                        dense[nme].append((f, v))
                for nme, d in dense.items():
                    lb.put_dense(node, nme, d)
            if cp.animated:
                vis = [(f, 1.0 if k < tonum(unwrap(cp.at(f / self.tl.fps))) else 0.0) for f in range(self.tl.nframes + 1)]
                holder = E("Node", id=lb.id(key, "repv", k), name=f"{r.name} {k + 1} · visible")
                node.add(holder)
                lb.put_dense(holder, "opacity", vis, hold=True)
                self.emit_copy(sub, holder, blend, k)
            else:
                self.emit_copy(sub, node, blend, k)
        if cp.animated:
            lb.note("converted", r.name, f"repeater with animated copies → {n_max} copies shown by hold keys")

    def emit_copy(self, sub, node, blend, k):
        lb = self.lb
        lb.copy_suffix.append(k)
        try:
            self.emit(sub, node, (), blend)
        finally:
            lb.copy_suffix.pop()

    def repeat_matrix(self, T, offset, k, n, t):
        def v(name, dim=None, default=0.0):
            p = T[name]
            if p is None:
                return default
            val = unwrap(p.at(t)) if p.animated else p.static(None, default)
            if dim is None:
                return tonum(val) if not isinstance(val, list) else tonum(val[0])
            return tonum(val[dim]) if isinstance(val, list) else tonum(val)
        a = (v("anchor", 0), v("anchor", 1))
        p = (v("position", 0), v("position", 1))
        s = (v("scale", 0, 100) / 100, v("scale", 1, 100) / 100)
        r = v("rotation")

        def step(u):
            m = geom.translate(-a[0], -a[1])
            m = geom.mul(geom.scale(1 + (s[0] - 1) * u, 1 + (s[1] - 1) * u), m)
            m = geom.mul(geom.rotate_deg(r * u), m)
            m = geom.mul(geom.translate(a[0] + p[0] * u, a[1] + p[1] * u), m)
            return m
        j = k + offset
        m = geom.IDENT
        full = int(math.floor(j)) if j >= 0 else int(math.ceil(j))
        frac = j - full
        st = step(1.0 if full >= 0 else 1.0)
        if full < 0:
            st = geom.invert(st) or geom.IDENT
        for _ in range(abs(full)):
            m = geom.mul(st, m)
        if abs(frac) > 1e-9:
            m = geom.mul(step(frac), m)
        o1, o2 = v("op1", None, 100) / 100, v("op2", None, 100) / 100
        o = o1 if n <= 1 else o1 + (o2 - o1) * k / (n - 1)
        return m, o

    # ---------------------------------------------------------------- shapes
    def emit_shape(self, paints, container, blend):
        """paths under different path operations cannot share a Shape (an effect applies to the whole paint)"""
        sigs = []
        for pr, _n in paints[0][3]:
            sig = tuple(id(m) for m in pr.mods)
            if sig not in sigs:
                sigs.append(sig)
        if len(sigs) <= 1:
            return self._emit_shape(paints, container, blend)
        for j, sig in enumerate(sigs):
            sub = [(e[0], e[1], e[2], [x for x in e[3] if tuple(id(m) for m in x[0].mods) == sig], e[4]) for e in paints]
            self.lb.copy_suffix.append(f"fx{j}")
            try:
                self._emit_shape(sub, container, blend)
            finally:
                self.lb.copy_suffix.pop()

    def _emit_shape(self, paints, container, blend):
        lb = self.lb
        first = paints[0]
        _, it, groups, prs, key = first
        shape = E("Shape", id=lb.id(key, "s"), name=" + ".join(p[1].name for p in paints))
        if blend and blend != "srcOver":
            shape.set("blendModeValue", blend)
        elif lb.blend and lb.blend != "srcOver":
            shape.set("blendModeValue", lb.blend)
        reps = self._paint_reps(first)
        target = container
        if reps:
            lb.note("approx", it.name, "paint below a Repeater: the repeater copies the painted shape (overlaps of "
                                       "copies are not merged into one fill)")
        # geometry (Merge Paths: subtract -> holes under the clockwise rule, exclude -> even-odd)
        n_paths = 0
        merge_mode = None
        base_cw = True
        clips = []                      # Merge Paths 'Intersect': the paths after the first clip it
        for pr, _nreps in prs:
            rel = pr.groups[len(groups):]
            for el in self.path_elements(pr, rel, key):
                if pr.merge is not None and pr.merge[1] == 4 and pr.merge[2] > 0:
                    merge_mode = 4
                    clips.append(el)
                    continue
                if pr.merge is not None:
                    merge_mode = pr.merge[1]
                    if pr.merge[1] == 3 and pr.merge[2] > 0:
                        el.set("isHole", True)
                    elif pr.merge[1] == 3:
                        base_cw = _clockwise(el)
                shape.add(el)
                n_paths += 1
        if n_paths == 0:
            return
        if merge_mode == 3 and (n_paths == 1 or not base_cw):
            # one path left (the others disabled): nothing to subtract. A counter-clockwise base draws nothing under
            # Rive's clockwise rule (broadcast-test's skull: only its outline showed) -> even-odd, winding-free
            for c in shape.children:
                if c.tag == "PointsPath" and c.attrs.get("isHole"):
                    del c.attrs["isHole"]
            merge_mode = None if n_paths == 1 else 5
        # paints: AE first = on top; Rive last paint = on top
        for e in reversed(paints):
            self.emit_paint(e, shape)
        if merge_mode in (3, 5):
            for c in shape.children:
                if c.tag == "Fill":
                    c.set("fillRule", "clockwise" if merge_mode == 3 else "evenOdd")
            if any(c.tag == "Stroke" for c in shape.children):
                lb.note("approx", it.name, "stroke of merged paths: each path is stroked (not the merged outline)")
        if clips:
            # AE intersects the merged paths: Rive draws the first, clipped by each of the others (a node holding
            # one ClippingShape per path clips to their intersection); the sources are unpainted shapes beside it
            wrap = E("Node", id=lb.id(key, "isect"), name=shape.name + " · intersect")
            wrap.add(shape)
            for j, el in enumerate(clips):
                src = wrap.add(E("Shape", id=lb.id(key, "isect", j), name=f"{shape.name} · intersect path {j + 2}"))
                src.add(el)
                wrap.add(E("ClippingShape", id=lb.id(key, "isect", j, "c"), name=f"Intersect {j + 2}", sourceId=src.id))
            if any(c.tag == "Stroke" for c in shape.children):
                lb.note("approx", it.name, "stroke of intersected paths: the first path's stroke, clipped")
            lb.note("converted", it.name, f"Merge Paths 'Intersect' → first path clipped by {len(clips)} path(s)")
            shape = wrap
        if reps:
            self.wrap_reps(reps, shape, target, blend)
        else:
            target.add(shape)

    def _paint_reps(self, e):
        _, it, groups, prs, key = e
        reps = None
        for pr, n in prs:
            r = pr.reps[:n]
            if reps is None:
                reps = r
            elif r != reps:
                return reps
        return reps or []

    def wrap_reps(self, reps, shape, container, blend):
        """paint below one or more repeaters: repeat the shape (static repeaters only; animated → at t=0)"""
        lb = self.lb
        r, key = reps[-1]
        T = {n: self.prop(r.sub("ADBE Vector Repeater Transform").property(mn), r.name + " " + n) for n, mn in (
            ("anchor", "ADBE Vector Repeater Anchor"), ("position", "ADBE Vector Repeater Position"),
            ("scale", "ADBE Vector Repeater Scale"), ("rotation", "ADBE Vector Repeater Rotation"),
            ("op1", "ADBE Vector Repeater Opacity 1"), ("op2", "ADBE Vector Repeater Opacity 2"))}
        n = int(math.ceil(tonum(self.prop(r.sub("ADBE Vector Repeater Copies"), "copies").static(None, 3))))
        off = tonum(self.prop(r.sub("ADBE Vector Repeater Offset"), "offset").static(None, 0))
        if any(p.animated for p in T.values()):
            lb.note("approx", r.name, "animated repeater under a paint: copies placed at t=0")
        wrap = E("Node", id=lb.id(key, "prep", shape.id), name=r.name)
        container.add(wrap)
        from copy import deepcopy
        for k in range(max(0, min(n, 500))):
            m, o = self.repeat_matrix(T, off, k, n, 0.0)
            x, y, rot, sx, sy, _ = geom.decompose(m)
            node = wrap.add(E("Node", id=lb.id(key, "prep", shape.id, k), name=f"{r.name} {k + 1}", x=x, y=y,
                              rotation=rot, scaleX=sx, scaleY=sy))
            if abs(o - 1) > 1e-6:
                node.set("opacity", o)
            node.add(shape if k == 0 else lb.clone(shape, f"prep{k}"))

    # ---------------------------------------------------------------- paths
    def path_elements(self, pr, rel_groups, paint_key=""):
        """Rive path elements for one AE path (with the transforms of the groups between it and its paint)."""
        lb, it = self.lb, pr.item
        # AE's own vertex order and start point matter to the trims and to the path effects
        need_points = bool(pr.trims) or pr.round is not None or bool(rel_groups) or bool(pr.mods)
        direction = 1
        d = it.sub("ADBE Vector Shape Direction")
        if d is not None:
            try:
                direction = int(tonum(d.value))
            except Exception:
                direction = 1
        key = f"{pr.key}@{paint_key}"            # a path drawn by two Shapes is two elements
        if it.kind == "rect" and not need_points:
            return [self.rect_el(it, key)]
        if it.kind == "ellipse" and not need_points:
            return [self.ellipse_el(it, key)]
        if rel_groups:
            # drawn by a paint of an outer group: the path goes through its groups' transforms (animated or not)
            fn0, props = self.path_fn(it, direction)
            if fn0 is None:
                return []
            gprops, rotating = self._group_props(rel_groups)

            def fn(t):
                m = geom.IDENT
                for g in rel_groups:
                    m = geom.mul(m, self._group_matrix(g, t))
                return geom.transform_path(fn0(t), m)
            src = PathSource.from_fn(fn, props + gprops, self.tl, force_frames=rotating)
        else:
            src = self.path_source(it, direction)
        if src is None:
            return []
        el = lb.points_path(key, it.name, src, geom.IDENT, round_prop=pr.round)
        return [el] if el is not None else []

    def _group_props(self, groups):
        out, rotating = [], False
        for g in groups:
            tr = g.sub("ADBE Vector Transform Group")
            if tr is None:
                continue
            for mn in ("ADBE Vector Position", "ADBE Vector Anchor", "ADBE Vector Scale", "ADBE Vector Rotation",
                       "ADBE Vector Skew", "ADBE Vector Skew Axis"):
                p = tr.property(mn)
                if p is None:
                    continue
                ap = self.prop(p, f"{g.name} {mn}")
                if ap.animated:
                    out.append(ap)
                    if mn in ("ADBE Vector Rotation", "ADBE Vector Skew", "ADBE Vector Skew Axis"):
                        rotating = True
        return out, rotating

    def _group_animated(self, g):
        tr = g.sub("ADBE Vector Transform Group")
        if tr is None:
            return False
        return any(self.prop(tr.property(mn), mn).animated for mn in (
            "ADBE Vector Position", "ADBE Vector Anchor", "ADBE Vector Scale", "ADBE Vector Rotation"))

    def _group_matrix(self, g, t):
        tr = g.sub("ADBE Vector Transform Group")
        if tr is None:
            return geom.IDENT

        def val(mn, default):
            p = tr.property(mn)
            if p is None:
                return default
            v = unwrap(self.prop(p, mn).at(t))
            return v
        pos = val("ADBE Vector Position", [0, 0])
        anc = val("ADBE Vector Anchor", [0, 0])
        sc = val("ADBE Vector Scale", [100, 100])
        rot = tonum(val("ADBE Vector Rotation", 0.0))
        sk = tonum(val("ADBE Vector Skew", 0.0))
        ska = tonum(val("ADBE Vector Skew Axis", 0.0))
        return geom.trs((tonum(pos[0]), tonum(pos[1])), rot, (tonum(sc[0]) / 100, tonum(sc[1]) / 100),
                        (tonum(anc[0]), tonum(anc[1])), sk, ska)

    def rect_el(self, it, key):
        lb = self.lb
        el = E("Rectangle", id=lb.id(key, "r"), name=it.name)
        size = self.prop(it.sub("ADBE Vector Rect Size"), it.name + " size")
        pos = self.prop(it.sub("ADBE Vector Rect Position"), it.name + " position")
        rnd = self.prop(it.sub("ADBE Vector Rect Roundness"), it.name + " roundness")
        lb.put(el, "x", pos, 0)
        lb.put(el, "y", pos, 1)
        lb.put(el, "width", size, 0, force=True)
        lb.put(el, "height", size, 1, force=True)
        # AE caps the roundness at half the smaller side at each instant; Rive does the same by itself (measured,
        # CLI 1.2.0: radius 50 on a 40-wide rectangle = radius 20). Capping here with the size at t = 0 lost the
        # rounding of a rectangle keyed from width 0 (Infinity's Web Elements 01)
        if rnd.animated:
            lb.put(el, "cornerRadiusTL", rnd, None)
        else:
            r = tonum(rnd.static(None))
            if r > 0:
                el.set("cornerRadiusTL", r)
        return el

    def ellipse_el(self, it, key):
        lb = self.lb
        el = E("Ellipse", id=lb.id(key, "e"), name=it.name)
        size = self.prop(it.sub("ADBE Vector Ellipse Size"), it.name + " size")
        pos = self.prop(it.sub("ADBE Vector Ellipse Position"), it.name + " position")
        lb.put(el, "x", pos, 0)
        lb.put(el, "y", pos, 1)
        lb.put(el, "width", size, 0, force=True)
        lb.put(el, "height", size, 1, force=True)
        return el

    def path_source(self, it, direction):
        """-> PathSource: static path, or path samples with their key structure"""
        if it.kind == "path":
            p = self.prop(it.sub("ADBE Vector Shape"), it.name + " path")
            return PathSource.from_prop(p, direction)
        fn, props = self.path_fn(it, direction)
        if fn is None:
            return None
        return PathSource.from_fn(fn, props, self.tl)

    def path_fn(self, it, direction):
        """-> (path at time t, [AProp]) for any AE path item"""
        k = it.kind
        if k == "path":
            ap = self.prop(it.sub("ADBE Vector Shape"), it.name + " path")

            def fnp(t):
                v = unwrap(ap.at(t))
                p = _sv_path(v if isinstance(v, ShapeValue) else _to_sv(v))
                return geom.reverse(p) if direction == 3 else p
            return fnp, [ap]
        if k == "rect":
            size = self.prop(it.sub("ADBE Vector Rect Size"), it.name + " size")
            pos = self.prop(it.sub("ADBE Vector Rect Position"), it.name + " position")
            rnd = self.prop(it.sub("ADBE Vector Rect Roundness"), it.name + " roundness")

            def fn(t):
                sv, pv, rv = unwrap(size.at(t)), unwrap(pos.at(t)), tonum(unwrap(rnd.at(t)))
                return geom.rect_path((tonum(pv[0]), tonum(pv[1])), (tonum(sv[0]), tonum(sv[1])), rv, direction)
            return fn, [size, pos, rnd]
        if k == "ellipse":
            size = self.prop(it.sub("ADBE Vector Ellipse Size"), it.name + " size")
            pos = self.prop(it.sub("ADBE Vector Ellipse Position"), it.name + " position")

            def fn(t):
                sv, pv = unwrap(size.at(t)), unwrap(pos.at(t))
                return geom.ellipse_path((tonum(pv[0]), tonum(pv[1])), (tonum(sv[0]), tonum(sv[1])), direction)
            return fn, [size, pos]
        if k == "star":
            names = {"type": "ADBE Vector Star Type", "points": "ADBE Vector Star Points",
                     "pos": "ADBE Vector Star Position", "rot": "ADBE Vector Star Rotation",
                     "ir": "ADBE Vector Star Inner Radius", "or": "ADBE Vector Star Outer Radius",
                     "is": "ADBE Vector Star Inner Roundess", "os": "ADBE Vector Star Outer Roundess"}
            P = {n: self.prop(it.sub(mn), it.name + " " + n) for n, mn in names.items() if it.sub(mn) is not None}

            def g(n, t, default=0.0):
                p = P.get(n)
                if p is None:
                    return default
                return unwrap(p.at(t))

            def fn(t):
                pv = g("pos", t, [0, 0])
                return geom.star_path(int(tonum(g("type", t, 1))), tonum(g("points", t, 5)), (tonum(pv[0]), tonum(pv[1])),
                                      tonum(g("rot", t)), tonum(g("ir", t, 50)), tonum(g("or", t, 100)),
                                      tonum(g("is", t)), tonum(g("os", t)), direction)
            return fn, list(P.values())
        return None, []

    # ---------------------------------------------------------------- paints
    def emit_paint(self, e, shape):
        lb = self.lb
        _, it, groups, prs, key = e
        kind = it.kind
        stroke = kind in ("stroke", "gstroke")
        tag = "Stroke" if stroke else "Fill"
        paint = E(tag, id=lb.id(key, "p"), name=it.name)
        op_mn = "ADBE Vector Stroke Opacity" if stroke else "ADBE Vector Fill Opacity"
        op = self.prop(it.sub(op_mn), it.name + " opacity")
        taper = None
        if stroke:
            w = self.prop(it.sub("ADBE Vector Stroke Width"), it.name + " width")
            lb.put(paint, "thickness", w, None, force=True)
            cap = it.sub("ADBE Vector Stroke Line Cap")
            join = it.sub("ADBE Vector Stroke Line Join")
            cv = {1: "butt", 2: "round", 3: "square"}.get(int(tonum(cap.value)) if cap is not None else 1, "butt")
            jv = {1: "miter", 2: "round", 3: "bevel"}.get(int(tonum(join.value)) if join is not None else 1, "miter")
            if cv != "butt":
                paint.set("cap", cv)
            if jv != "miter":
                paint.set("join", jv)
            tp = it.sub("ADBE Vector Stroke Taper")
            taper = tp if tp is not None and self._taper_on(tp) else None
        else:
            rule = it.sub("ADBE Vector Fill Rule")
            if rule is not None and int(tonum(rule.value)) == 2:
                paint.set("fillRule", "evenOdd")
        if kind in ("fill", "stroke"):
            col = self.prop(it.sub("ADBE Vector Stroke Color" if stroke else "ADBE Vector Fill Color"), it.name + " color")
            sc = E("SolidColor", id=lb.id(key, "c"), name="Color")
            paint.add(sc)
            lb.color_paint(sc, col, op, shape)
        else:
            self.gradient(it, key, paint, op, shape)
        # Rive applies a paint's effects in child order (measured, CLI 1.2.0): AE's path operations, then its
        # Trim Paths (stacked trims chain, a trimmed fill closes on its chord as in AE), then the stroke's dashes,
        # which AE draws last as part of the stroke
        mods = prs[0][0].mods if prs else []
        for i, m in enumerate(mods):
            paint.add(self.path_fx(m, key, i))
        trims = []
        for pr, _n in prs:
            for tr in pr.trims:
                if tr not in trims:
                    trims.append(tr)
        for j, tr in enumerate(trims):
            self.trim(tr, key, paint, j)
        if stroke:
            multi = bool(prs) and all(self._multi_segment(pr) for pr, _n in prs)
            self.dashes(it, key, paint, luau=bool(trims) or bool(mods), trims=trims if multi else ())
            if taper is not None:
                self._tapered(it, key, paint, taper)
        shape.add(paint)

    def _tapered(self, it, key, paint, tp):
        """AE stroke Taper -> the Stroke becomes a Fill drawing the stroke's band with a varying width (Luau
        ae_taper), after its trims and dashes"""
        lb = self.lb
        from .rml import prop_key
        pk_th = prop_key("Stroke", "thickness")
        th_track = lb.anim.tracks.pop((paint.id, pk_th), None)
        if th_track is not None:
            lb.anim.order = [k for k in lb.anim.order if k != (paint.id, pk_th)]
        width = paint.attrs.get("thickness", 1.0)
        cap = {"round": 1.0, "square": 2.0}.get(paint.attrs.get("cap", "butt"), 0.0)
        for a in ("thickness", "cap", "join"):
            paint.attrs.pop(a, None)
        paint.tag = "Fill"
        kids = {getattr(q, "match_name", ""): q for q in _kids(tp)}

        def ap(mn):
            q = kids.get(mn)
            return self.prop(q, it.name + " " + mn.replace("ADBE Vector Taper ", "taper ")) if q is not None else None
        units_p = kids.get("ADBE Vector Taper Length Units")
        px = units_p is not None and int(tonum(units_p.value)) == 2
        fx = paint.add(E("ScriptedPathEffect", id=lb.id(key, "taper"), name="Taper", scriptAssetId=lb.conv.script("ae_taper")))
        wi = fx.add(E("ScriptInputNumber", id=lb.id(key, "taper", "w"), name="width", propertyValue=float(tonum(width))))
        if th_track is not None:
            lb.anim.tracks[(wi.id, prop_key("ScriptInputNumber", "propertyValue"))] = th_track
            lb.anim.order.append((wi.id, prop_key("ScriptInputNumber", "propertyValue")))
        fx.add(E("ScriptInputNumber", id=lb.id(key, "taper", "cap"), name="cap", propertyValue=cap))
        fx.add(E("ScriptInputNumber", id=lb.id(key, "taper", "u"), name="units", propertyValue=1.0 if px else 0.0))
        for name, mn, mn_px in (("startLen", "ADBE Vector Taper Start Length", "ADBE Vector Taper StartWidthPx"),
                                ("endLen", "ADBE Vector Taper End Length", "ADBE Vector Taper EndWidthPx"),
                                ("startWidth", "ADBE Vector Taper Start Width", None),
                                ("endWidth", "ADBE Vector Taper End Width", None),
                                ("startEase", "ADBE Vector Taper Start Ease", None),
                                ("endEase", "ADBE Vector Taper End Ease", None)):
            src = ap(mn_px if (px and mn_px) else mn)
            inp = fx.add(E("ScriptInputNumber", id=lb.id(key, "taper", name), name=name, propertyValue=0.0))
            if src is not None:
                lb.put(inp, "propertyValue", src, None, 1.0 if (px and mn_px) else 0.01, force=True)
        wave = ap("ADBE Vector Taper Wave Amount")
        if wave is not None and (wave.animated or abs(tonum(wave.static(None, 0))) > 1e-6):
            lb.note("approx", it.name, "stroke Wave (width ripple) not reproduced")
        if any(c.tag == "DashPath" or (c.tag == "ScriptedPathEffect" and c.name == "Dashes") for c in paint.children):
            lb.note("approx", it.name, "taper on a dashed stroke: each dash is tapered (AE tapers the whole path)")
        lb.note("converted", it.name, "stroke taper → filled band of varying width (Luau ae_taper, measured in AE)")

    @staticmethod
    def _multi_segment(pr):
        """closed, or more than one segment (3+ vertices, or subdivided by a path operation such as Twist)"""
        if pr.item.kind != "path" or pr.mods:
            return True
        p = pr.item.sub("ADBE Vector Shape")
        try:
            ks = list(getattr(p, "keyframes", []) or [])
            sv = _to_sv(ks[0].value if ks else p.value)
            return bool(getattr(sv, "closed", False)) or len(getattr(sv, "v", []) or []) >= 3
        except Exception:
            return False

    # ---------------------------------------------------------------- path operations (Luau)
    def path_fx(self, it, key, i):
        """AE Zig Zag / Offset Paths / Pucker & Bloat / Twist / Roughen / Wiggle Paths -> ScriptedPathEffect.
        Inside the paint (Fill / Stroke), like a Trim Path: nested directly in a Shape the build cannot be
        re-imported (measured, CLI 1.0.1 → 1.2.0, the docs' own example included)."""
        lb = self.lb
        el = E("ScriptedPathEffect", id=lb.id(key, "fx", i), name=it.name,
               scriptAssetId=lb.conv.script(PATH_FX[it.kind]))
        for name, val, dim, a, b in self._fx_inputs(it):
            inp = el.add(E("ScriptInputNumber", id=lb.id(key, "fx", i, name), name=name))
            if isinstance(val, (int, float)):
                inp.set("propertyValue", float(val))
            elif isinstance(val, _Dense):
                lb.put_dense(inp, "propertyValue", val.dense)
            else:
                lb.put(inp, "propertyValue", val, dim, a, b, force=True)
        if it.kind in ("twist", "roughen", "wiggle paths"):
            lb.note("approx", it.name, f"{it.kind}: rebuilt in Luau from AE's documented behaviour (not bit-exact)")
        else:
            lb.note("converted", it.name, f"{it.kind} → Luau path effect (lottie-web's algorithm)")
        return el

    def _fx_inputs(self, it):
        """-> [(input name, AProp | number, dim, scale, offset)] matched to the Luau script's Input<number> fields"""
        kids = {getattr(p, "match_name", ""): p for p in _kids(it.prop)}

        def find(*words):
            for mn, p in kids.items():
                low = mn.lower()
                if all(w in low for w in words):
                    return p
            return None

        def ap(p, what):
            return self.prop(p, f"{it.name} {what}") if p is not None else None

        def num(p, default):
            if p is None:
                return default
            try:
                return tonum(unwrap(p.value))
            except Exception:
                return default
        k = it.kind
        out = []

        def add(name, p, default, dim=None, a=1.0, b=0.0):
            if p is None:
                out.append((name, default, None, 1.0, 0.0))
            else:
                out.append((name, ap(p, name), dim, a, b))
        if k == "zig zag":
            add("size", find("zigzag size"), 10)
            add("ridges", find("zigzag detail"), 5)
            add("points", find("zigzag points"), 1)
        elif k == "offset":
            add("amount", find("offset amount"), 10)
            out.append(("join", num(find("line join"), 1), None, 1.0, 0.0))
            add("miterLimit", find("miter limit"), 4)
            add("copies", find("copies"), 1)
            add("copyOffset", find("copy offset"), 0)
        elif k == "pucker":
            add("amount", find("puckerbloat amount") or find("amount"), 0)
        elif k == "twist":
            add("angle", find("twist angle"), 0)
            c = find("twist center")
            add("centerX", c, 0, 0)
            add("centerY", c, 0, 1)
        else:   # roughen / wiggle paths
            add("size", find("size"), 5)
            add("detail", find("detail"), 10)
            add("points", find("points"), 1)
            add("seed", find("seed"), 0)
            add("correlation", find("correlation"), 50)
            if k == "wiggle paths":
                out.append(("phase", self._wiggle_phase(it, find("temporal freq") or find("wiggles"),
                                                        find("temporal phase")), None, 1.0, 0.0))
        return out

    def _wiggle_phase(self, it, wps, tphase):
        """Wiggle Paths time: phase(t) = ∫ wiggles/s dt + temporal phase / 360, as a dense track"""
        w = self.prop(wps, it.name + " wiggles/s") if wps is not None else None
        tp = self.prop(tphase, it.name + " temporal phase") if tphase is not None else None
        acc, prev, dense = 0.0, None, []
        for f in range(self.tl.nframes + 1):
            t = f / self.tl.fps
            rate = tonum(unwrap(w.at(t))) if w is not None else 2.0
            if prev is not None:
                acc += rate / self.tl.fps
            prev = rate
            ph = tonum(unwrap(tp.at(t))) / 360.0 if tp is not None else 0.0
            dense.append((f, acc + ph))
        return _Dense(dense)

    @staticmethod
    def _taper_on(tp):
        for p in _kids(tp):
            try:
                if getattr(p, "match_name", "").endswith(("Start Length", "End Length", "StartWidthPx", "EndWidthPx")) and (
                        abs(tonum(p.value)) > 1e-6 or len(getattr(p, "keyframes", []) or []) > 1):
                    return True
            except Exception:
                continue
        return False

    def _trim_reversed(self, trims):
        """per frame: 1 where AE runs the trimmed path backwards. With Start > End, AE's Trim Paths keeps the part
        from Start back to End, and the stroke's dashes start from that end — measured (AE 26.5) on closed ellipses
        (Arcs.aep + probes, mirrored groups included) and on twisted open lines (Blowups 6); a one-segment open line
        keeps its direction (probes, Complicated_Elements 4). Rive's TrimPath always keeps it forwards. Stacked trims
        flip it in turn. Called only for paths with more than one segment."""
        props = [(self.prop(tr.sub("ADBE Vector Trim Start"), tr.name + " start"),
                  self.prop(tr.sub("ADBE Vector Trim End"), tr.name + " end")) for tr in trims]
        dense = []
        for f in range(self.tl.nframes + 1):
            t = f / self.tl.fps
            rev = 0
            for s, e in props:
                if tonum(unwrap(s.at(t))) > tonum(unwrap(e.at(t))) + 1e-9:
                    rev ^= 1
            dense.append((f, float(rev)))
        return dense

    def dashes(self, it, key, paint, luau=False, trims=()):
        lb = self.lb
        grp = it.sub("ADBE Vector Stroke Dashes")
        if grp is None:
            return
        # py-aep synthesizes every dash slot with its default (10) even when the user added none: keep the stored ones
        kids = {getattr(p, "match_name", ""): p for p in _kids(grp) if _stored(p)}
        seq = []
        for i in (1, 2, 3):
            d, g = kids.get(f"ADBE Vector Stroke Dash {i}"), kids.get(f"ADBE Vector Stroke Gap {i}")
            if d is None:
                break
            dv = self.prop(d, f"dash {i}")
            gv = self.prop(g, f"gap {i}") if g is not None else dv
            seq.append((dv, gv))
        if not seq or all(abs(tonum(dv.static(None))) < 1e-9 and not dv.animated for dv, _ in seq):
            return
        offp = kids.get("ADBE Vector Stroke Offset")
        off = self.prop(offp, "dash offset") if offp is not None else None
        if luau:
            # after a TrimPath (or a path effect) Rive's DashPath goes wrong: a first dash shorter than ~10 units, or
            # any offset, fills the next gap (measured, CLI 1.2.0) — the dashes are cut in Luau instead (ae_dash)
            el = E("ScriptedPathEffect", id=lb.id(key, "dashfx"), name="Dashes", scriptAssetId=lb.conv.script("ae_dash"))
            for i, (dv, gv) in enumerate(seq[:3]):
                for nm, ap in ((f"d{i + 1}", dv), (f"g{i + 1}", gv)):
                    inp = el.add(E("ScriptInputNumber", id=lb.id(key, "dashfx", nm), name=nm))
                    lb.put(inp, "propertyValue", ap, None, force=True)
            el.add(E("ScriptInputNumber", id=lb.id(key, "dashfx", "n"), name="count", propertyValue=float(min(3, len(seq)))))
            inp = el.add(E("ScriptInputNumber", id=lb.id(key, "dashfx", "off"), name="offset", propertyValue=0.0))
            if off is not None:
                lb.put(inp, "propertyValue", off, None, force=True)
            rev = self._trim_reversed(trims) if trims else []
            if any(v for _, v in rev):
                inp = el.add(E("ScriptInputNumber", id=lb.id(key, "dashfx", "rev"), name="reverse"))
                lb.put_dense(inp, "propertyValue", rev, hold=True)
            paint.add(el)
            return
        dp = E("DashPath", id=lb.id(key, "dash"), name="Dashes")
        if off is not None:
            # AE's offset moves the pattern back along the path, Rive's forward (measured: AE +25 on 40/40 = Rive -25)
            lb.put(dp, "offset", off, None, -1.0)
        for i, (dv, gv) in enumerate(seq):
            de = dp.add(E("Dash", id=lb.id(key, "dash", i, "d"), name=f"Dash {i + 1}"))
            lb.put(de, "length", dv, None, force=True)
            ge = dp.add(E("Dash", id=lb.id(key, "dash", i, "g"), name=f"Gap {i + 1}"))
            lb.put(ge, "length", gv, None, force=True)
        paint.add(dp)

    def trim(self, tr, key, paint, j=0):
        lb = self.lb
        el = E("TrimPath", id=lb.id(key, "trim", *([j] if j else [])), name=tr.name)
        s = self.prop(tr.sub("ADBE Vector Trim Start"), tr.name + " start")
        e = self.prop(tr.sub("ADBE Vector Trim End"), tr.name + " end")
        o = self.prop(tr.sub("ADBE Vector Trim Offset"), tr.name + " offset")
        lb.put(el, "start", s, None, 0.01, force=True)
        lb.put(el, "end", e, None, 0.01, force=True)
        lb.put(el, "offset", o, None, 1 / 360.0)
        mode = tr.sub("ADBE Vector Trim Type")
        m = int(tonum(mode.value)) if mode is not None else 1
        el.set("modeValue", "synchronized" if m == 1 else "sequential")
        paint.add(el)

    def gradient(self, it, key, paint, op, shape):
        lb = self.lb
        gtype = it.sub("ADBE Vector Grad Type")
        radial = gtype is not None and int(tonum(gtype.value)) == 2
        g = E("RadialGradient" if radial else "LinearGradient", id=lb.id(key, "grad"), name="Gradient")
        sp = self.prop(it.sub("ADBE Vector Grad Start Pt"), it.name + " start")
        ep = self.prop(it.sub("ADBE Vector Grad End Pt"), it.name + " end")
        lb.put(g, "startX", sp, 0, force=True)
        lb.put(g, "startY", sp, 1, force=True)
        lb.put(g, "endX", ep, 0, force=True)
        lb.put(g, "endY", ep, 1, force=True)
        lb.put(g, "opacity", op, None, 0.01, default=1.0)
        hl = it.sub("ADBE Vector Grad HiLite Length")
        if radial and hl is not None and abs(tonum(hl.value)) > 1e-3:
            lb.note("approx", it.name, "radial gradient highlight (focal point) is not in Rive: centred")
        cols = it.sub("ADBE Vector Grad Colors")
        grad = None
        keyed = None
        if cols is not None:
            try:
                grad = cols.value
                kfs = list(getattr(cols, "keyframes", []) or [])
                if len(kfs) > 1:
                    keyed = kfs
                    grad = kfs[0].value
                if lb.tl.engine.has_expr(cols):
                    lb.note("approx", it.name, "expression on gradient colours ignored")
            except Exception:
                grad = None
        if keyed:
            self._keyed_gradient(it, key, g, cols, keyed)
        else:
            stops = gradient_stops(grad)
            for i, (pos, rgba) in enumerate(stops):
                st = g.add(E("GradientStop", id=lb.id(key, "gs", i), position=float(pos), colorValue=argb(rgba)))
                lb.colors.append(("stop", st))
        paint.add(g)

    def _keyed_gradient(self, it, key, g, cols, kfs):
        """animated gradient colours: every key's stops are sampled at the union of all keys' stop positions, and
        each GradientStop's colour is keyed with AE's key timing and eases (AE interpolates the stops' colours)"""
        lb = self.lb
        per_key = [gradient_stops(k.value) for k in kfs]
        pts = sorted({round(p, 4) for st in per_key for p, _ in st})

        def at(stops, x):
            if x <= stops[0][0]:
                return stops[0][1]
            if x >= stops[-1][0]:
                return stops[-1][1]
            for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
                if p0 <= x <= p1:
                    u = (x - p0) / (p1 - p0) if p1 > p0 else 0.0
                    return [c0[i] + (c1[i] - c0[i]) * u for i in range(4)]
            return stops[-1][1]
        ap = self.prop(cols, it.name + " gradient colours")
        try:
            eases = progress_keys(ap.keys, self.tl.fps, ap.stretch, ap.p) if ap.mode == "keys" else None
        except Exception:
            eases = None
        if eases is None or len(eases) != len(kfs):
            fps = self.tl.fps
            eases = [(k.time * fps, "linear", None) for k in kfs]
        for i, x in enumerate(pts):
            vals = [argb(at(st, x)) for st in per_key]
            st = g.add(E("GradientStop", id=lb.id(key, "gs", i), position=float(x), colorValue=vals[0]))
            lb.colors.append(("stop", st))
            if len(set(vals)) > 1:
                keys = [(f, v, interp, ease) for v, (f, interp, ease) in zip(vals, eases)]
                lb.anim.put(st.id, "GradientStop", "colorValue", keys, kind="color")
        lb.note("converted", it.name, f"animated gradient colours → {len(pts)} stops keyed ({len(kfs)} keys)")


class _Dense:
    """a per-frame track computed here (not an AE property)"""

    def __init__(self, dense):
        self.dense = dense


def gradient_stops(grad):
    """py-aep Gradient -> [(position, [r,g,b,a])], colour and alpha stops merged, midpoints approximated by a stop."""
    if grad is None:
        return [(0.0, [0, 0, 0, 1]), (1.0, [1, 1, 1, 1])]
    try:
        cs = sorted(((float(s.offset), float(s.midpoint), list(s.color)) for s in grad.color_stops), key=lambda x: x[0])
        as_ = sorted(((float(s.offset), float(s.midpoint), float(s.alpha)) for s in grad.alpha_stops), key=lambda x: x[0])
    except Exception:
        return [(0.0, [0, 0, 0, 1]), (1.0, [1, 1, 1, 1])]
    if not cs:
        cs = [(0.0, 0.5, [0, 0, 0]), (1.0, 0.5, [1, 1, 1])]
    if not as_:
        as_ = [(0.0, 0.5, 1.0), (1.0, 0.5, 1.0)]

    def sample(stops, x, blend):
        if x <= stops[0][0]:
            return stops[0][2]
        if x >= stops[-1][0]:
            return stops[-1][2]
        for (p0, m0, v0), (p1, _m1, v1) in zip(stops, stops[1:]):
            if p0 <= x <= p1:
                u = (x - p0) / (p1 - p0) if p1 > p0 else 0.0
                # AE midpoint: the 50 % blend sits at m0 of the span (a power curve through that point)
                m = min(0.999, max(0.001, m0))
                u = u ** (math.log(0.5) / math.log(m)) if 0 < u < 1 else u
                return blend(v0, v1, u)
        return stops[-1][2]
    pts = sorted({round(p, 4) for p, _, _ in cs} | {round(p, 4) for p, _, _ in as_} |
                 {round(p0 + (p1 - p0) * m0, 4) for (p0, m0, _), (p1, _, _) in zip(cs, cs[1:]) if abs(m0 - 0.5) > 0.01} |
                 {round(p0 + (p1 - p0) * m0, 4) for (p0, m0, _), (p1, _, _) in zip(as_, as_[1:]) if abs(m0 - 0.5) > 0.01})
    out = []
    for x in pts:
        c = sample(cs, x, lambda a, b, u: [a[i] + (b[i] - a[i]) * u for i in range(3)])
        a = sample(as_, x, lambda a, b, u: a + (b - a) * u)
        out.append((x, list(c)[:3] + [a]))
    return out


# ====================================================================== path sources
class PathSource:
    """static path, or AE keyed path (exact keys + progress eases), or per-frame samples"""

    def __init__(self):
        self.static = None          # path tuple
        self.keys = None            # [(time, path)]
        self.eases = None           # progress_keys output aligned with keys
        self.frames = None          # [path per frame]

    @staticmethod
    def from_prop(ap, direction):
        ps = PathSource()
        conv = (lambda sv: geom.reverse(_sv_path(sv)) if direction == 3 else _sv_path(sv))
        if ap.mode == "static":
            v = ap.static(None)
            if not isinstance(v, ShapeValue):
                return None
            ps.static = conv(v)
        elif ap.mode == "keys":
            ps.keys = [(k.time, conv(_to_sv(k.value))) for k in ap.keys]
            ps.eases = progress_keys(ap.keys, ap.tl.fps, ap.stretch, ap.p)
        else:
            ps.frames = [conv(unwrap(v)) if isinstance(unwrap(v), ShapeValue) else conv(_to_sv(v)) for v in ap.frames]
        return ps

    @staticmethod
    def from_fn(fn, props, tl, force_frames=False):
        """a parametric path (rect / ellipse / star). When its animated parameters are keyed AE properties sharing
        their key times (or only one is animated), the path is keyed at those times with that ease — the vertices
        of a rectangle or an ellipse are affine in its parameters, so this is exact."""
        ps = PathSource()
        anim = [p for p in props if p.animated]
        if not anim:
            ps.static = fn(0.0)
            return ps
        if not force_frames and all(p.mode == "keys" for p in anim):
            times = [tuple(round(k.time, 5) for k in p.keys) for p in anim]
            eases = [progress_keys(p.keys, tl.fps, p.stretch, p.p) for p in anim]
            same = all(t == times[0] for t in times) and all([e[1:] for e in es] == [e[1:] for e in eases[0]] for es in eases)
            if (len(anim) == 1 or same) and times[0][0] >= 0:
                ps.keys = [(t, fn(t)) for t in times[0]]
                ps.eases = eases[0]
                return ps
        ps.frames = [fn(f / tl.fps) for f in range(tl.nframes + 1)]
        return ps

    def samples(self):
        if self.static is not None:
            return [self.static]
        if self.keys is not None:
            return [p for _, p in self.keys]
        return self.frames or []


def _to_sv(v):
    if isinstance(v, ShapeValue):
        return v
    from .aexpr import to_js
    return to_js(v)


def _sv_path(sv):
    if not isinstance(sv, ShapeValue):
        return geom.path([])
    return geom.path(sv.v, sv.i, sv.o, sv.closed)


# ====================================================================== bounds (sourceRectAtTime)
def layer_bounds(L, engine, comp, t, extents=False):
    root = L.property("ADBE Root Vectors Group")
    boxes = []

    def walk(group, m):
        for p in _kids(group):
            mn = getattr(p, "match_name", "")
            if not _enabled(p):
                continue
            if mn == "ADBE Vector Group":
                tr = p.property("ADBE Vector Transform Group")
                mm = m
                if tr is not None:
                    def g(n, d):
                        q = tr.property(n)
                        return unwrap(engine.value(q, L, comp, t)) if q is not None else d
                    pos, anc, sc = g("ADBE Vector Position", [0, 0]), g("ADBE Vector Anchor", [0, 0]), g("ADBE Vector Scale", [100, 100])
                    rot = tonum(g("ADBE Vector Rotation", 0))
                    mm = geom.mul(m, geom.trs((tonum(pos[0]), tonum(pos[1])), rot, (tonum(sc[0]) / 100, tonum(sc[1]) / 100),
                                              (tonum(anc[0]), tonum(anc[1]))))
                walk(p.property("ADBE Vectors Group"), mm)
            elif mn in PATHS:
                def g2(n, d):
                    q = p.property(n)
                    return unwrap(engine.value(q, L, comp, t)) if q is not None else d
                kind = PATHS[mn]
                if kind == "rect":
                    sz, ps_ = g2("ADBE Vector Rect Size", [0, 0]), g2("ADBE Vector Rect Position", [0, 0])
                    pth = geom.rect_path((tonum(ps_[0]), tonum(ps_[1])), (tonum(sz[0]), tonum(sz[1])), 0)
                elif kind == "ellipse":
                    sz, ps_ = g2("ADBE Vector Ellipse Size", [0, 0]), g2("ADBE Vector Ellipse Position", [0, 0])
                    pth = geom.ellipse_path((tonum(ps_[0]), tonum(ps_[1])), (tonum(sz[0]), tonum(sz[1])))
                elif kind == "path":
                    sv = g2("ADBE Vector Shape", None)
                    pth = _sv_path(sv) if isinstance(sv, ShapeValue) else geom.path([])
                else:
                    r = tonum(g2("ADBE Vector Star Outer Radius", 0))
                    ps_ = g2("ADBE Vector Star Position", [0, 0])
                    pth = geom.ellipse_path((tonum(ps_[0]), tonum(ps_[1])), (2 * r, 2 * r))
                boxes.append(geom.bounds([geom.transform_path(pth, m)]))
    if root is not None:
        walk(root, geom.IDENT)
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
