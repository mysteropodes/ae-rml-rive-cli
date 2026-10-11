"""`ae pull`, what was ADDED in After Effects: new layers, new vector groups in a layer the build made, new paths and
paints in a shape. pull.py reads the edits of what the build made; this module writes new RML elements for the rest.

- A layer with no rml2ae tag in an animation comp (or AE's copy of a tagged layer: Duplicate keeps the comment, the
  original keeps the lowest layer id):
  - null -> Node; solid -> Shape (Rectangle + Fill); shape layer -> a Shape when it holds one plain vector group
    (identity transform, anchor 0: what the build itself makes of a Shape), else a Node holding one element per vector
    group (a group of paths and paints a Shape, a group of groups a Node; the build makes one shape layer of it again).
  - Transform from the layer (position less the parent layer's anchor point, rotation, scale, opacity, hidden flag),
    keyed in the comp's animation when AE keys it (eases as for transforms in pull.py); a layer anchor point goes into
    the children's coordinates.
  - Parent: the AE parent layer's element when it is a node, the artboard when unparented. Parented to a shape (a Rive
    shape has no child node): a sibling of that shape when it is static and unrotated, its transform composed.
  - Place: the RML child order is the AE stack (Rive draws the first child on top): before the nearest sibling below
    it in AE, else after the nearest one above, else at the end of the parent.
  - Text, footage, precomps, adjustment layers, cameras and lights are listed, not pulled; so are effects, masks, track
    mattes, blend modes, 3D and in / out points of a pulled layer.
  - The AE layer is then tagged with the new element's id (`_pull_tags.jsx`, run by `ae pull` and the panel), so the
    next pull reads it as any built layer and the next build replaces it in place by what the RML now says.
- A vector group added to a shape layer the build grouped (industry layout, `<project>.groups.json`): a new Shape or
  Node under the group's parent element, at its rank among its siblings; the converter's new group map knows it.
- Paths or paints added to a shape's group (one more fill, a second path): the existing ones are matched in order (kind
  and geometry / colour), the extra ones written at the same rank, paints in reverse order as the build reads them.
"""
import math

from .model import is_a
from .pull import fmt, keys_to_rive
from .pullshape import (AE_PATHS, PEPS, VERTEX_PROPS, VERTEX_TAGS, ae_at, color_key_xml, fits, hexc, path_interps, pkey,
                        vertex_type, vertex_values)

CAPS = {1: "butt", 2: "round", 3: "square"}
JOINS = {1: "miter", 2: "round", 3: "bevel"}
NOT_PULLED = {"text": "a text layer", "footage": "a footage layer", "precomp": "a precomp layer", "adjust": "an adjustment layer",
              "camera": "a camera", "light": "a light"}
END_TAGS = ("LinearAnimation", "StateMachine")       # an artboard's children after its drawables


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def attrs(d):
    return "".join(f' {k}="{v}"' for k, v in d.items() if v is not None)


def comp_of(v, dim):
    return v[dim] if dim is not None else v


def shifted(rec, dim, shift):
    """a dump record with `shift` taken off one component (value and keys)"""
    if not shift:
        return rec

    def f(v):
        if dim is None:
            return v - shift
        return list(v[:dim]) + [v[dim] - shift] + list(v[dim + 1:])
    out = dict(rec, v=f(rec["v"]))
    if rec.get("k"):
        out["k"] = [dict(k, v=f(k["v"])) for k in rec["k"]]
    return out


def static2(rec, default=(0.0, 0.0)):
    """a 2-D value at time 0 (anchor points: not keyed by the build, read as a constant)"""
    if rec is None:
        return list(default)
    return [rec["v"][0], rec["v"][1]]


def node_like(el):
    """an element that can hold child nodes in Rive and in the build (a Node, the artboard, a bone, a layout)"""
    return el.tag == "Artboard" or (is_a(el.tag, "Node") and not is_a(el.tag, "Drawable") and not is_a(el.tag, "Path"))


def align_groups(L, gids, proj):
    """the group map of a layer as the build wrote it ({path: id}) -> as the layer is now in AE, and the parent paths
    whose groups could not be told apart. The build names each group after its element: a group added (or moved)
    among its siblings shifts the ranks after it, the names say which is which. A level where a known group is
    missing by name (renamed or deleted in AE) keeps the ranks when the counts still agree, else it is unsure."""
    groups = L.get("groups") or []
    if not gids or not groups or any("n" not in g for g in groups):
        return gids, set()
    by = {}
    for g in groups:
        by.setdefault(tuple(g["path"][:-1]), []).append(g)
    out, unsure = {}, set()

    def walk(ae, rml):
        kids = sorted(by.get(ae, []), key=lambda g: g["path"][-1])
        known = sorted(((p, i) for p, i in gids.items() if len(p) == len(rml) + 1 and p[:len(rml)] == rml), key=lambda x: x[0][-1])
        pairs, j = [], 0
        for p, oid in known:
            el = proj.by_id.get(oid)
            k = j
            while k < len(kids) and (el is None or kids[k]["n"] != el.name):
                k += 1
            if k == len(kids):
                pairs = None
                break
            pairs.append((tuple(kids[k]["path"]), p, oid))
            j = k + 1
        if pairs is None:
            if len(kids) != len(known):
                unsure.add(ae)
            pairs = [(tuple(g["path"]), p, oid) for g, (p, oid) in zip(kids, known)]
        for path, p, oid in pairs:
            out[path] = oid
            walk(path, p)
    walk((), ())
    return out, unsure


class Gen:
    """XML of new elements from dump records, for one animation (`anim`); keys go to the Adder's queue. Ids come from
    the Adder by a stable key, so the same AE item read in two windows of a state chain is the same element."""

    def __init__(self, ad, anim, uid, writes=True):
        self.ad, self.anim, self.uid, self.writes = ad, anim, uid, writes
        self.notes = []
        self.keyed = 0
        self.shapes = 0
        self.child_tags = []              # (AE comp id, AE layer id, tag) of added layers written inside this one

    def oid(self, *key):
        return self.ad.oid((self.uid,) + key)

    def frame(self, t):
        return t * self.anim.fps * self.anim.speed

    def key(self, oid, pk, keys, kxml=None):
        self.ad.keys.append((self.anim, oid, pk, keys, kxml))
        self.keyed += 1

    def num(self, rec, dim, unit, oid, tag, name, shift=0.0, affine=(0.0, 1.0)):
        """the static value (Rive units) of one property; its keys queued when AE keys it"""
        if rec is None:
            return None
        r = shifted(rec, dim, shift)
        a, b = affine
        ks = r.get("k") or []
        if len(ks) >= 2:
            keys = [(f, a + b * v, i, e) for f, v, i, e in keys_to_rive(ks, dim, self.anim.fps, self.anim.speed, unit)]
            if any(abs(k[1] - keys[0][1]) > 1e-6 for k in keys):     # (a 2-D AE key may hold one component still)
                self.key(oid, pkey(tag, name), keys)
            return keys[0][1]
        v = comp_of(ks[0]["v"] if ks else r["v"], dim)
        return a + b * v * unit

    @staticmethod
    def put(d, name, v, default):
        if v is not None and abs(v - default) > 1e-9:
            d[name] = fmt(v)

    # ---- transforms
    def transform(self, oid, tag, rec, layer, shift=(0.0, 0.0), compose=None):
        """x, y, rotation, scaleX, scaleY, opacity of a layer (`layer`) or a vector group record"""
        d = {}
        ex, ey, esx, esy, eop = compose or (0.0, 0.0, 1.0, 1.0, 1.0)
        if layer and rec.get("sep"):
            px, py = (rec["px"], None), (rec["py"], None)
        else:
            px, py = (rec["p"], 0), (rec["p"], 1)
        self.put(d, "x", self.num(px[0], px[1], 1.0, oid, tag, "x", shift[0], (ex, esx)), 0.0)
        self.put(d, "y", self.num(py[0], py[1], 1.0, oid, tag, "y", shift[1], (ey, esy)), 0.0)
        self.put(d, "rotation", self.num(rec.get("r"), None, math.pi / 180, oid, tag, "rotation"), 0.0)
        self.put(d, "scaleX", self.num(rec.get("s"), 0, 0.01, oid, tag, "scaleX", affine=(0.0, esx)), 1.0)
        self.put(d, "scaleY", self.num(rec.get("s"), 1, 0.01, oid, tag, "scaleY", affine=(0.0, esy)), 1.0)
        self.put(d, "opacity", self.num(rec.get("o"), None, 0.01, oid, tag, "opacity", affine=(0.0, eop)), 1.0)
        return d

    @staticmethod
    def identity(g):
        """a vector group whose transform changes nothing (and is not keyed)"""
        def flat(rec, want):
            return rec is None or (not rec.get("k") and all(abs(a - b) < 1e-6 for a, b in zip(
                rec["v"] if isinstance(rec["v"], list) else [rec["v"]], want)))
        return (flat(g.get("p"), [0, 0]) and flat(g.get("a"), [0, 0]) and flat(g.get("r"), [0]) and
                flat(g.get("s"), [100, 100]) and flat(g.get("o"), [100]))

    # ---- shape contents
    def items(self, items, shift, key):
        """paths in AE order, then the paints reversed (Rive draws the later paint on top, AE the item higher in the
        group): a list of XML strings"""
        paths, paints = [], []
        for n, it in enumerate(items):
            m = it["m"]
            if not it.get("on", True):
                self.notes.append(f"'{it.get('n', m)}' is switched off in AE: not pulled")
                continue
            if m == "path":
                paths.append(self.points(it, shift, key + (n,)))
            elif m in ("rect", "ellipse"):
                paths.append(self.parametric(it, shift, key + (n,)))
            elif m in ("fill", "stroke"):
                paints.append(self.paint(it, key + (n,)))
            else:
                self.notes.append(f"'{it.get('n', m)}' ({m.replace('ADBE Vector ', '')}) not pulled")
        return paths + paints[::-1]

    def parametric(self, it, shift, key):
        tag = "Rectangle" if it["m"] == "rect" else "Ellipse"
        oid = self.oid(*key)
        d = {}
        self.put(d, "width", self.num(it["size"], 0, 1.0, oid, tag, "width"), -1)
        self.put(d, "height", self.num(it["size"], 1, 1.0, oid, tag, "height"), -1)
        self.put(d, "x", self.num(it["pos"], 0, 1.0, oid, tag, "x", shift[0]), 0.0)
        self.put(d, "y", self.num(it["pos"], 1, 1.0, oid, tag, "y", shift[1]), 0.0)
        if tag == "Rectangle" and it.get("rd") is not None:
            self.put(d, "cornerRadiusTL", self.num(it["rd"], None, 1.0, oid, tag, "cornerRadiusTL"), 0.0)
        d.update(name=esc(it.get("n") or tag), id=oid)
        return f"<{tag}{attrs(d)}/>"

    def points(self, it, shift, key):
        rec = it["p"]
        oid = self.oid(*key)
        ks = rec.get("k") or []
        first = ks[0]["v"] if ks else rec["v"]
        n = len(first["vs"])
        if len(ks) >= 2 and any(len(k["v"]["vs"]) != n for k in ks):
            self.notes.append(f"'{it.get('n', 'path')}': its keys change the vertex count, only its first key is pulled")
            ks = []
        d = {"isClosed": "true" if first["c"] else "false", "name": esc(it.get("n") or "Path"), "id": oid}
        out = []
        if len(ks) >= 2:
            frames = [self.frame(k["t"]) for k in ks]
            interp = path_interps(ks)
            for j in range(n):
                tag = next((t for t in VERTEX_TAGS if all(fits(t, k["v"]["in"][j], k["v"]["out"][j]) for k in ks)), "CubicDetachedVertex")
                vid = self.oid(*key, "v", j)
                prev = None
                rows = {name: [] for name in VERTEX_PROPS[tag]}
                for k, f, (ip, ease) in zip(ks, frames, interp):
                    vals = vertex_values(tag, [k["v"]["vs"][j][0] - shift[0], k["v"]["vs"][j][1] - shift[1]],
                                         k["v"]["in"][j], k["v"]["out"][j], prev)
                    prev = vals
                    for name in rows:
                        rows[name].append((f, vals[name], ip, ease))
                va = {}
                for name, lst in rows.items():
                    va[name] = fmt(lst[0][1])
                    eps = 0.0005 if "otation" in name else PEPS
                    if any(abs(x[1] - lst[0][1]) > eps for x in lst):
                        self.key(vid, pkey(tag, name), lst)
                out.append(f"<{tag}{attrs(va)} id=\"{vid}\"/>")
        else:
            for j in range(n):
                i, o = first["in"][j], first["out"][j]
                tag = vertex_type(i, o)
                vals = vertex_values(tag, [first["vs"][j][0] - shift[0], first["vs"][j][1] - shift[1]], i, o)
                out.append(f"<{tag}{attrs({k: fmt(v) for k, v in vals.items()})}/>")
        return f"<PointsPath{attrs(d)}>{''.join(out)}</PointsPath>"

    def paint(self, it, key):
        stroke = it["m"] == "stroke"
        tag = "Stroke" if stroke else "Fill"
        pid, cid = self.oid(*key), self.oid(*key, "c")
        c, o = it["c"], it.get("o") or {"v": 100}
        ck, ok = c.get("k") or [], o.get("k") or []

        def at(t):
            rgb = ae_at(ck, t) if ck else c["v"]
            a = (ae_at(ok, t) if ok else o["v"]) / 100.0
            return hexc(rgb[0], rgb[1], rgb[2], a)
        if len(ck) >= 2 or len(ok) >= 2:
            times = []
            for k in sorted(ck + ok, key=lambda k: k["t"]):
                if not times or k["t"] - times[-1] > 1e-4:
                    times.append(k["t"])
            byt = {}
            for k in ok + ck:                       # a colour key's interpolation wins over an opacity key's
                byt[round(k["t"], 4)] = k["o"]
            keys = [(self.frame(t), at(t), "hold" if byt.get(round(t, 4)) == "h" else "linear", None) for t in times]
            self.key(cid, pkey("SolidColor", "colorValue"), keys, color_key_xml)
            value = keys[0][1]
        else:
            value = at(0)
        d = {}
        if stroke:
            self.put(d, "thickness", self.num(it.get("w"), None, 1.0, pid, "Stroke", "thickness"), 1.0)
            cap = CAPS.get(int(round((it.get("cap") or {"v": 1})["v"])), "butt")
            join = JOINS.get(int(round((it.get("join") or {"v": 1})["v"])), "miter")
            if cap != "butt":
                d["cap"] = cap
            if join != "miter":
                d["join"] = join
        d.update(name=esc(it.get("n") or tag), id=pid)
        return f'<{tag}{attrs(d)}><SolidColor colorValue="{value}" id="{cid}"/></{tag}>'

    # ---- groups and layers
    def group(self, g, groups, shift, key, compose=None):
        """one AE vector group -> a Shape (paths and paints) or a Node (groups), its descendants inside"""
        path = g["path"]
        subs = [h for h in groups if len(h["path"]) == len(path) + 1 and h["path"][:len(path)] == path]
        name = esc(g.get("n") or "Group")
        anchor = static2(g.get("a"))
        if g.get("a") and g["a"].get("k"):
            self.notes.append(f"group '{g.get('n')}': its keyed anchor point is read at 0 s")
        oid = self.oid(*key)
        kids = [it for it in g.get("items") or [] if it["m"] in AE_PATHS or it["m"] in ("fill", "stroke")]
        if not subs:
            d = self.transform(oid, "Shape", g, False, shift, compose)
            d.update(name=name, id=oid)
            if not g.get("enabled", True):
                d["drawableFlags"] = "1"
            self.shapes += 1
            return f"<Shape{attrs(d)}>{''.join(self.items(g.get('items') or [], anchor, key))}</Shape>"
        if not g.get("enabled", True):
            self.notes.append(f"group '{g.get('n')}' is switched off in AE: not pulled")
            return ""
        d = self.transform(oid, "Node", g, False, shift, compose)
        d.update(name=name, id=oid)
        inner = [self.group(h, groups, anchor, key + (k,)) for k, h in enumerate(subs)]
        if kids:
            self.shapes += 1
            sid = self.oid(*key, "own")
            inner.append(f"<Shape name=\"{name}\" id=\"{sid}\">{''.join(self.items(g.get('items') or [], anchor, key + ('own',)))}</Shape>")
        return f"<Node{attrs(d)}>{''.join(inner)}</Node>"

    def layer(self, L, shift, above=(), below=(), compose=None):
        """an added layer -> (tag, XML); `above` / `below`: XML of its child layers above / below it in the stack"""
        kind = L["kind"]
        name = esc(L.get("name") or kind)
        oid = self.oid("layer")
        anchor = static2(L.get("a"))
        if L.get("a") and L["a"].get("k"):
            self.notes.append("its keyed anchor point is read at 0 s")
        content, single = [], None
        if kind == "shape":
            groups = L.get("groups") or []
            tops = [g for g in groups if len(g["path"]) == 1]
            loose = [it for it in L.get("items") or [] if it["m"] in AE_PATHS or it["m"] in ("fill", "stroke")]
            if (len(tops) == 1 and not loose and not above and not below and tops[0].get("enabled", True)
                    and not any(len(g["path"]) > 1 for g in groups) and self.identity(tops[0]) and abs(anchor[0]) < 1e-6 and abs(anchor[1]) < 1e-6):
                single = (tops[0].get("items") or [], tops[0]["path"])
            else:
                content = [self.group(g, groups, anchor, ("g",) + tuple(g["path"])) for g in tops]
                if loose:
                    sid = self.oid("own")
                    self.shapes += 1
                    content.append(f"<Shape name=\"{name}\" id=\"{sid}\">{''.join(self.items(L.get('items') or [], anchor, ('own',)))}</Shape>")
        elif kind == "solid":
            w, h, rgb = L["solid"][0], L["solid"][1], L["solid"][2]
            rect = (f'<Rectangle name="{name}" id="{self.oid("rect")}" width="{fmt(w)}" height="{fmt(h)}" '
                    f'x="{fmt(w / 2 - anchor[0])}" y="{fmt(h / 2 - anchor[1])}"/>')
            fill = f'<Fill name="Fill" id="{self.oid("fill")}"><SolidColor colorValue="{hexc(rgb[0], rgb[1], rgb[2], 1)}" id="{self.oid("fillc")}"/></Fill>'
            if not above and not below:
                single = rect + fill
            else:
                self.shapes += 1
                content = [f'<Shape name="{name}" id="{self.oid("own")}">{rect}{fill}</Shape>']
        if single is not None:
            d = self.transform(oid, "Shape", L, True, shift, compose)
            d.update(name=name, id=oid)
            if not L.get("enabled", True):
                d["drawableFlags"] = "1"
            self.shapes += 1
            body = single if isinstance(single, str) else "".join(self.items(single[0], (0.0, 0.0), ("g",) + tuple(single[1])))
            return "Shape", f"<Shape{attrs(d)}>{body}</Shape>"
        d = self.transform(oid, "Node", L, True, shift, compose)
        d.update(name=name, id=oid)
        if not L.get("enabled", True):
            self.notes.append("hidden in AE: a Rive node has no hidden flag, pulled visible")
        return "Node", f"<Node{attrs(d)}>{''.join(list(above) + content + list(below))}</Node>"


class Adder:
    """New RML elements of one pull: generated per comp, inserted once every other edit is written (insertions move
    the id-less elements other edits find by rank), then their keys."""

    def __init__(self, proj, file_of, new_id, changes, pulled, dry):
        self.proj, self.file_of, self.new_id = proj, file_of, new_id
        self.changes, self.pulled, self.dry = changes, pulled, dry
        self.ids = {}                     # stable key -> new id
        self.done = set()                 # created already (a state chain's windows read the same AE item)
        self.inserts = []                 # (file, spec, sort key, xml)
        self.keys = []                    # (anim, oid, property key, keys, key xml)
        self.tags = []                    # (AE comp id, AE layer id, tag)
        self.placed = []                  # (comp tag, layer tag) of those layers: the manifest says to rebuild them
        self.new_ids = set()

    def oid(self, key):
        if key not in self.ids:
            self.ids[key] = self.new_id()
            self.new_ids.add(self.ids[key])
        return self.ids[key]

    def insert(self, f, spec, sort, xml, nid=None):
        """`spec`: ("before" | "after", element), ("end", container) or ("new", new element id): next to a new sibling,
        where it goes; same place: ascending `sort`"""
        if xml:
            self.inserts.append((f, spec, sort, xml, nid))

    # ---- placement helpers
    @staticmethod
    def child_of(el, target):
        """the ancestor-or-self of `el` that is a direct child of `target` (None when `el` is not under it)"""
        while el is not None and el.parent is not target:
            el = el.parent
        return el

    def element_of_tag(self, tag):
        """a layer tag -> (element, its role): main 'rive:<id>', or a run of siblings 'rive:<first>+run'"""
        if not tag or not tag.startswith("rive:"):
            return None, None
        body = tag[5:]
        if "+" in body:
            oid, suffix = body.split("+", 1)
            if suffix == "run":
                return self.proj.by_id.get(oid), "run"
            return None, None
        return self.proj.by_id.get(body), "main"

    def spec_among(self, target, entries, me, placed):
        """where a new child of `target` goes: next to the nearest sibling below `me` in AE (index > me), else above.
        `entries`: [(index, element or None, new element id or None)]; already placed new siblings count too."""
        below = sorted((e for e in entries if e[0] > me), key=lambda e: e[0])
        above = sorted((e for e in entries if e[0] < me), key=lambda e: -e[0])
        for lst, where in ((below, "before"), (above, "after")):
            for idx, el, nid in lst:
                if nid is not None and nid in placed:
                    return ("new", nid)
                if el is not None:
                    c = self.child_of(el, target)
                    if c is not None:
                        return (where, c)
        return ("end", target)

    # ---- added layers of one comp
    def comp(self, ab, anim, comp, first_anim, multi):
        added = comp.get("added") or []
        if not added:
            return
        parts = comp["tag"].split("|")
        where = f"{ab.name} / {anim.name}"
        if len(parts) > 2:
            return                          # a clip precomp: its layers are the build's (the clipped subtree)
        stack = {e["i"]: e for e in comp.get("stack") or []}
        by_i = {L["i"]: L for L in added}
        f = self.file_of(ab.id)
        if f is None:
            return
        skipped = set()
        for L in sorted(added, key=lambda L: L["i"]):
            why = NOT_PULLED.get(L["kind"])
            if why is None and L.get("isMatte"):
                why = "the track matte of another layer"
            if why is None and L.get("threeD"):
                why = "a 3D layer"
            if why:
                skipped.add(L["i"])
                if (comp.get("id"), L["lid"]) not in self.done:
                    self.changes.append(f"{where}: layer '{L.get('name')}' added in AE: not pulled ({why})")
                    self.done.add((comp.get("id"), L["lid"]))
        # parents: an added parent holds its added children; the others hang from an element (or are not pulled)
        roots = []
        for L in sorted(added, key=lambda L: -L["i"]):          # bottom first: a placed new sibling anchors the next
            if L["i"] in skipped:
                continue
            p = L.get("par") or 0
            chain = set()
            while p in by_i and p not in chain and p not in skipped:
                chain.add(p)
                p = by_i[p].get("par") or 0
            if p in skipped:
                self.changes.append(f"{where}: layer '{L.get('name')}' added in AE: not pulled (its parent layer is not)")
                skipped.add(L["i"])
                continue
            if (L.get("par") or 0) in by_i:
                continue                      # written inside its added parent
            roots.append(L)
        placed = set()
        for L in roots:
            host = self.host(ab, stack, L.get("par") or 0)
            uid = ("layer", comp.get("id"), L["lid"])
            if host is None:
                if uid not in self.done:
                    self.done.add(uid)
                    self.changes.append(f"{where}: layer '{L.get('name')}' added in AE: not pulled (its parent is not a node "
                                        "Rive can hang it from, or a shape that moves)")
                continue
            target, shift, compose, sibling = host
            gen = Gen(self, anim, uid)
            tag, xml = self.layer_tree(gen, L, by_i, skipped, shift, compose)
            nid = gen.oid("layer")
            first = uid not in self.done
            if first:
                self.done.add(uid)
                entries = []
                for i, e in stack.items():
                    if i == L["i"]:
                        continue
                    if i in by_i:
                        nb = self.ids.get((("layer", comp.get("id"), by_i[i]["lid"]), "layer"))
                        entries.append((i, None, nb))
                    else:
                        el, _role = self.element_of_tag(e.get("c"))
                        entries.append((i, el, None))
                spec = self.spec_among(target, entries, L["i"], placed)
                if sibling is not None and spec[0] == "end":
                    spec = ("before", sibling)
                self.insert(f, spec, L["i"], xml, nid)
                placed.add(nid)
                self.tags.append((comp.get("id"), L["lid"], "rive:" + nid))
                self.tags += gen.child_tags
                ctag = comp.get("realtag") or comp["tag"]
                self.placed += [(ctag, t[2]) for t in [(0, 0, "rive:" + nid)] + gen.child_tags]
                bits = [f"{gen.shapes} shape(s)"] if gen.shapes else []
                if gen.keyed:
                    bits.append(f"{gen.keyed} keyed propert{'y' if gen.keyed == 1 else 'ies'}")
                extra = []
                if L.get("copy"):
                    extra.append("a copy of a built layer")
                for k, label in (("fx", "effects"), ("masks", "masks")):
                    if L.get(k):
                        extra.append(f"its {label} are not pulled")
                if L.get("hasMatte"):
                    extra.append("its track matte is not pulled")
                if L.get("blend"):
                    extra.append("its blend mode is not pulled")
                if L.get("in", 0) > 1e-6 or (comp.get("dur") and L.get("out", 0) < comp["dur"] - 1e-6):
                    extra.append("its in / out points are not pulled")
                if multi and anim.id != first_anim:
                    extra.append(f"Rive shows it in every animation of {ab.name}")
                extra += gen.notes
                pname = target.name if target is not ab else ab.name
                self.changes.append(f"{where}: layer '{L.get('name')}' added in AE -> {tag} '{L.get('name')}' ({nid}) in {pname}"
                                    + (f" ({', '.join(bits)})" if bits else "") + ("; " + "; ".join(extra) if extra else ""))
                if target.id:
                    self.pulled.add(target.id)
            else:
                placed.add(nid)

    def layer_tree(self, gen, L, by_i, skipped, shift, compose):
        """XML of an added layer and of the added layers parented to it (their position less its anchor point)"""
        anchor = static2(L.get("a"))
        kids = sorted((K for K in by_i.values() if (K.get("par") or 0) == L["i"] and K["i"] not in skipped), key=lambda K: K["i"])
        above, below = [], []
        for K in kids:
            sub = Gen(self, gen.anim, ("layer", gen.uid[1], K["lid"]))
            _t, x = self.layer_tree(sub, K, by_i, skipped, anchor, None)
            gen.notes += [f"'{K.get('name')}': {n}" for n in sub.notes]
            gen.shapes += sub.shapes
            gen.keyed += sub.keyed
            (above if K["i"] < L["i"] else below).append(x)
            gen.child_tags += [(gen.uid[1], K["lid"], "rive:" + sub.oid("layer"))] + sub.child_tags
        return gen.layer(L, shift, above, below, compose)

    def host(self, ab, stack, par):
        """the AE parent layer index -> (Rive parent, position shift, composed transform, sibling to stay next to)"""
        if not par:
            return ab, (0.0, 0.0), None, None
        e = stack.get(par)
        if e is None:
            return None
        el, role = self.element_of_tag(e.get("c"))
        if el is None:
            return None
        a = e.get("a") or [0, 0]
        if role == "run":                     # a run layer sits at identity in its container's space
            return (el.parent, (a[0], a[1]), None, None) if el.parent is not None and node_like(el.parent) else None
        if node_like(el):
            return el, (a[0], a[1]), None, None
        # a leaf (shape, text, image): Rive hangs nothing from it — its sibling, the leaf's static transform composed
        moving = any(o == el.id for an in self.proj.animations.values() for (o, _pk) in an.keys)
        sx, sy = el.num("scaleX", 1), el.num("scaleY", 1)
        if moving or abs(el.num("rotation")) > 1e-9 or sx <= 0 or sy <= 0 or el.parent is None or not node_like(el.parent):
            return None
        comp = (el.num("x") - sx * a[0], el.num("y") - sy * a[1], sx, sy, el.num("opacity", 1))
        return el.parent, (0.0, 0.0), comp, el

    # ---- vector groups added to a layer the build grouped
    def groups(self, ab, anim, L, gids, unsure=()):
        """unknown group paths of a tagged layer with a group map -> new elements under their parent's element"""
        el, role = self.element_of_tag(L.get("tag") or ("rive:" + L["id"]))
        if el is None:
            return
        where = f"{ab.name} / {anim.name}"
        groups = L.get("groups") or []
        if not gids:
            # a layer of one element (a dedicated shape: its contents in group 1): a group beside it has no Rive place
            extra = [g for g in groups if g["path"] != [1] and len(g["path"]) == 1 or (len(g["path"]) == 2 and g["path"][0] == 1)]
            if is_a(el.tag, "Shape"):
                for g in extra:
                    uid = ("group", L.get("tag") or L["id"], tuple(g["path"]))
                    if uid not in self.done:
                        self.done.add(uid)
                        self.changes.append(f"{where}: group '{g.get('n')}' added in AE to '{L.get('name')}': not pulled (a Rive "
                                            "shape holds no group: draw it on its own layer)")
            return
        known = set(gids)
        for g in groups:
            path = tuple(g["path"])
            if path in known or any(path[:k] not in known for k in range(1, len(path))):
                continue                      # known, or inside an unknown group (written with it)
            parent_path = path[:-1]
            uid = ("group", L.get("tag") or L["id"], path)
            if parent_path in unsure:
                if uid not in self.done:
                    self.done.add(uid)
                    self.changes.append(f"{where}: group '{g.get('n')}' in '{L.get('name')}': not pulled (groups of that level were "
                                        "renamed or deleted in AE: which one is new is unclear)")
                continue
            if parent_path:
                target = self.proj.by_id.get(gids.get(parent_path))
            else:
                target = el.parent if role == "run" else el
            if target is None or not node_like(target):
                if uid not in self.done:
                    self.done.add(uid)
                    self.changes.append(f"{where}: group '{g.get('n')}' added in AE to '{L.get('name')}': not pulled "
                                        "(it is inside a shape: group it beside the shape, or draw it on its own layer)")
                continue
            gen = Gen(self, anim, uid)
            xml = gen.group(g, groups, (0.0, 0.0), ("g",))
            if uid in self.done:
                continue
            self.done.add(uid)
            sibs = [h for h in groups if len(h["path"]) == len(path) and tuple(h["path"][:-1]) == parent_path and tuple(h["path"]) != path]
            entries = [(h["path"][-1], self.proj.by_id.get(gids.get(tuple(h["path"]))), self.ids.get((("group", uid[1], tuple(h["path"])), "g")))
                       for h in sibs]
            f = self.file_of(target.id) if target.id else self.file_of(ab.id)
            spec = self.spec_among(target, entries, path[-1], {e[2] for e in entries if e[2]})
            self.insert(f, spec, path[-1], xml, gen.oid("g"))
            if target.id:
                self.pulled.add(target.id)
            self.changes.append(f"{where}: group '{g.get('n')}' added in AE to '{L.get('name')}' -> "
                                f"{'Node' if xml.startswith('<Node') else 'Shape'} ({gen.oid('g')}) in {target.name}"
                                + (f" ({gen.keyed} keyed propert{'y' if gen.keyed == 1 else 'ies'})" if gen.keyed else "")
                                + ("; " + "; ".join(gen.notes) if gen.notes else ""))

    # ---- paths / paints added inside an existing shape (pullshape.Contents finds them)
    def extra_items(self, shape, anim, items, label):
        """`items`: [(AE index, item, spec, sort)] new paths or paints of `shape`"""
        f = self.file_of(shape.id)
        for q, it, spec, sort in items:
            uid = ("item", shape.id, label, q)
            gen = Gen(self, anim, uid)
            xml = "".join(gen.items([it], (0.0, 0.0), ("i",)))
            if uid in self.done or not xml:
                continue
            self.done.add(uid)
            self.insert(f, spec, sort, xml)
            self.changes.append(f"{self.where(shape, anim)}: {it['m']} '{it.get('n', it['m'])}' added in AE"
                                + (f" ({gen.keyed} keyed propert{'y' if gen.keyed == 1 else 'ies'})" if gen.keyed else "")
                                + ("; " + "; ".join(gen.notes) if gen.notes else ""))
            self.pulled.add(shape.id)

    def where(self, shape, anim):
        ab = shape
        while ab is not None and ab.tag != "Artboard":
            ab = ab.parent
        return f"{ab.name if ab is not None else ''} / {anim.name}: {shape.name} ({shape.id})"

    # ---- writing
    def flush(self):
        """insert every new element (offsets taken on the final text, then written from the end), then their keys"""
        if self.dry or not self.inserts:
            return
        own = {nid: spec for _f, spec, _s, _x, nid in self.inserts if nid}

        def resolve(spec, depth=0):
            while spec[0] == "new" and depth < 50:   # next to a new sibling: where that one goes
                spec = own.get(spec[1], ("none",))
                depth += 1
            return spec
        by_file = {}
        for f, spec, sort, xml, _nid in self.inserts:
            by_file.setdefault(id(f), (f, []))[1].append((resolve(spec), sort, xml))
        for f, lst in by_file.values():
            # a self-closing container opens first (that moves the offsets after it: done before any is taken)
            for spec, _s, _x in lst:
                if spec[0] == "end":
                    sp = f.span(spec[1])
                    if sp is not None and sp[3]:
                        f.replace_children(spec[1], "")
            at = {}
            for spec, sort, xml in lst:
                off = self.offset(f, spec)
                if off is not None:
                    at.setdefault(off, []).append((sort, "\n" + xml if spec[0] == "after" else xml + "\n"))
            for off in sorted(at, reverse=True):
                f.text = f.text[:off] + "".join(x for _s, x in sorted(at[off], key=lambda t: t[0])) + f.text[off:]
                f.changed = True
        for anim, oid, pk, keys, kxml in self.keys:
            f = self.file_of(anim.id)
            if f is not None:
                f.set_keys(anim.id, oid, pk, keys, kxml)

    @staticmethod
    def offset(f, spec):
        kind = spec[0]
        if kind in ("before", "after"):
            sp = f.span(spec[1])
            if sp is None:
                return None
            return sp[0] if kind == "before" else sp[2]
        if kind == "end":
            el = spec[1]
            sp = f.span(el)
            if sp is None:
                return None
            if el.tag == "Artboard":
                tail = next((c for c in el.children if c.tag in END_TAGS), None)
                if tail is not None:
                    ts = f.span(tail)
                    if ts is not None:
                        return ts[0]
            return f.text.rindex("</", sp[1], sp[2])
        return None
