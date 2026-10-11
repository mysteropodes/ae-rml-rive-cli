"""Rive flex layouts (LayoutComponent + LayoutComponentStyle, solved by Yoga in the runtime) as a tree the After
Effects expressions solve again: the same subset of Yoga, written once in JavaScript (SOLVER_JS) and run by every
layout layer's expressions, so that the sizes, gaps and paddings stay editable (sliders on a "Layout · <artboard>"
null) and the boxes follow.

What the Rive runtime does, as measured on CI screenshots (rive --screenshot):
- the artboard is the root; with a style its defaults are Rive's (row, top left, fixed size), without one Yoga's
  (column, children stretched across);
- a LayoutComponent without a style ignores its width / height: it is sized by its layout children (0 for a leaf);
- a child's x / y are added on top of the position the layout gives it; non-layout children (shapes, texts...) sit at
  their x / y from the layout's top-left corner;
- a LayoutComponent's own Fill / Stroke paint its box, behind its children;
- a Rectangle / Ellipse whose Shape sits directly in a LayoutComponent with a style takes that layout's size (its x / y
  and origin stay); in one without a style it keeps its own size.

Anything outside the subset (wrap, grid, absolute position, percent units, min / max, aspect ratio, nested artboard
layouts...) returns a reason: the converter then keeps the CLI replay for the artboard's layouts.
"""
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET

from .model import is_a

DIRS = {"column": 0, "columnReverse": 1, "row": 2, "rowReverse": 3}
ALIGN = {n: i for i, n in enumerate(("topLeft", "topCenter", "topRight", "centerLeft", "center", "centerRight",
                                     "bottomLeft", "bottomCenter", "bottomRight", "spaceBetweenStart",
                                     "spaceBetweenCenter", "spaceBetweenEnd"))}
SCALE = {"fixed": 0, "fill": 1, "hug": 2}

# style attributes the solver reads, and those that change nothing it places
SUPPORTED = {"name", "id", "parentId", "flexDirectionValue", "layoutAlignmentType", "layoutWidthScaleType",
             "layoutHeightScaleType", "gapHorizontal", "gapVertical",
             "paddingLeft", "paddingTop", "paddingRight", "paddingBottom",
             "marginLeft", "marginTop", "marginRight", "marginBottom",
             "borderLeft", "borderTop", "borderRight", "borderBottom",
             "linkCornerRadius", "cornerRadiusTL", "cornerRadiusTR", "cornerRadiusBL", "cornerRadiusBR",
             "animationStyleType", "interpolationType", "interpolatorId", "interpolationTime", "hugUnbounded"}
# attributes accepted at their default value only
DEFAULTS = {"flexWrapValue": ("0", "noWrap"), "positionTypeValue": ("0", "1", "relative", "static"),
            "layoutTypeValue": ("0", "flex"), "displayValue": ("0", "flex"), "directionValue": ("0", "1", "inherit", "ltr", "LTR"),
            "overflowValue": ("0", "visible"), "intrinsicallySizedValue": ("false", "0"),
            "positionLeft": ("0",), "positionTop": ("0",), "positionRight": ("0",), "positionBottom": ("0",),
            "flexBasis": ("0",), "aspectRatio": ("0",), "minWidth": ("0",), "minHeight": ("0",),
            "maxWidth": ("0",), "maxHeight": ("0",), "justifySelfValue": ("0", "auto"), "justifyItemsValue": ("0", "auto")}
POINT = ("1", "point")


def _enum(v, names, default):
    if v in (None, ""):
        return default
    if v in names:
        return names[v]
    try:
        return int(float(v))
    except ValueError:
        return default


def _num(v, default=0.0):
    try:
        return float(v) if v not in (None, "") else float(default)
    except ValueError:
        return float(default)


def style_of(project, el):
    sid = el.get("styleId")
    st = project.by_id.get(sid) if sid else None
    return st if st is not None and st.tag == "LayoutComponentStyle" else None


def _style_reason(st):
    for k, v in st.attrs.items():
        if k in SUPPORTED:
            continue
        if k.endswith("UnitsValue"):
            if v not in POINT:
                return f"{k}={v} (only point units are solved)"
            continue
        ok = DEFAULTS.get(k)
        if ok is None:
            return f"style attribute {k}"
        if v not in ok and not (ok == ("0",) and _num(v) == 0):
            return f"{k}={v}"
    return None


def sizing_layout(project, shape):
    """the LayoutComponent that sizes the parametric paths of this Shape (its parent, when it has a style), or None"""
    p = shape.parent
    if p is not None and is_a(p.tag, "LayoutComponent") and p.tag != "Artboard" and style_of(project, p) is not None:
        return p
    return None


def layout_nodes(ab):
    """the artboard's LayoutComponents, parents first"""
    return [e for e in ab.iter() if e is not ab and is_a(e.tag, "LayoutComponent")]


def tree(project, ab):
    """(T, index, reason): T the JSON-able tree the solver reads (node 0 is the artboard), index id(el) -> node;
    reason is None when the solver covers every layout of the artboard."""
    nodes = [ab] + layout_nodes(ab)
    if len(nodes) == 1:
        return None, {}, "no layout"
    index = {id(e): i for i, e in enumerate(nodes)}
    for e in ab.iter():
        if e.tag in ("NestedArtboardLayout", "ScriptedLayout", "LayoutParticipant", "LayoutNodeStyle"):
            return None, index, f"{e.tag} in a layout"
    out = []
    for i, e in enumerate(nodes):
        if i and id(e.parent) not in index:
            return None, index, f"{e.name}: a LayoutComponent under a {e.parent.tag}"
        if i and e.get("clip") in ("true", "1"):
            return None, index, f"{e.name}: clipped layout"
        st = style_of(project, e)
        n = {"k": [index[id(c)] for c in e.children if id(c) in index], "w": e.num("width"), "h": e.num("height"),
             "nm": e.name}
        if st is not None:
            why = _style_reason(st)
            if why:
                return None, index, f"{e.name}: {why}"
            g = st.get
            n["s"] = {"d": _enum(g("flexDirectionValue"), DIRS, 2), "a": _enum(g("layoutAlignmentType"), ALIGN, 0),
                      "sw": _enum(g("layoutWidthScaleType"), SCALE, 0), "sh": _enum(g("layoutHeightScaleType"), SCALE, 0),
                      "gx": _num(g("gapHorizontal")), "gy": _num(g("gapVertical")),
                      "p": [_num(g("padding" + s)) + _num(g("border" + s)) for s in ("Left", "Top", "Right", "Bottom")],
                      "m": [_num(g("margin" + s)) for s in ("Left", "Top", "Right", "Bottom")],
                      "fw": e.num("fractionalWidth", 1), "fh": e.num("fractionalHeight", 1)}
            if any(_enum(g(k), SCALE, 0) > 2 for k in ("layoutWidthScaleType", "layoutHeightScaleType")):
                return None, index, f"{e.name}: scale type"
        out.append(n)
    out[0]["w"], out[0]["h"] = ab.num("width", 1920), ab.num("height", 1080)
    if "s" in out[0]:
        out[0]["s"]["sw"] = out[0]["s"]["sh"] = 0
    return {"n": out}, index, None


# The solver: rlay(T, G) -> [[left, top, width, height] per node], left / top from the parent's top-left corner.
# G(i, key, default) reads an editable value (a slider in AE); keys: Width, Height, Gap, Padding Left/Top/Right/Bottom.
SOLVER_JS = r"""
function rlay(T, G) {
  var N = T.n, R = [], Q = [], i;
  function g(i, k, d) { return G ? G(i, k, d) : d; }
  for (i = 0; i < N.length; i++) {
    var n = N[i], s = n.s;
    if (!s) { Q.push({st: 0, d: 0, a: -1, sw: 2, sh: 2, W: 0, H: 0, p: [0, 0, 0, 0], m: [0, 0, 0, 0], gm: 0, fw: 0, fh: 0}); continue; }
    var row = s.d >= 2;
    Q.push({st: 1, d: s.d, a: s.a, sw: s.sw, sh: s.sh, W: g(i, "Width", n.w), H: g(i, "Height", n.h),
      p: [g(i, "Padding Left", s.p[0]), g(i, "Padding Top", s.p[1]), g(i, "Padding Right", s.p[2]), g(i, "Padding Bottom", s.p[3])],
      m: s.m, gm: g(i, "Gap", row ? s.gx : s.gy), fw: s.fw, fh: s.fh});
  }
  function al(q) {
    // [justify, align]: 0 start, 1 center, 2 end, 3 space between / stretch (cross)
    if (q.a < 0) return [0, 3];
    if (q.a >= 9) return [3, q.a - 9];
    var h = q.a % 3, v = Math.floor(q.a / 3);
    return q.d >= 2 ? [h, v] : [v, h];
  }
  function content(i) {
    var q = Q[i], k = N[i].k, row = q.d >= 2, m = 0, c = 0, j;
    for (j = 0; j < k.length; j++) {
      var s = intr(k[j]), cm = Q[k[j]].m;
      m += row ? s[0] + cm[0] + cm[2] : s[1] + cm[1] + cm[3];
      c = Math.max(c, row ? s[1] + cm[1] + cm[3] : s[0] + cm[0] + cm[2]);
    }
    if (k.length > 1) m += q.gm * (k.length - 1);
    return row ? [m + q.p[0] + q.p[2], c + q.p[1] + q.p[3]] : [c + q.p[0] + q.p[2], m + q.p[1] + q.p[3]];
  }
  function intr(i) {
    var q = Q[i], w = q.sw == 0 ? q.W : null, h = q.sh == 0 ? q.H : null;
    if (w === null || h === null) { var c = content(i); if (w === null) w = c[0]; if (h === null) h = c[1]; }
    return [w, h];
  }
  function lay(i, w, h) {
    R[i][2] = w; R[i][3] = h;
    var q = Q[i], k = N[i].k, n = k.length, j;
    if (!n) return;
    var row = q.d >= 2, rev = q.d == 1 || q.d == 3, A = al(q);
    var im = row ? w - q.p[0] - q.p[2] : h - q.p[1] - q.p[3], ic = row ? h - q.p[1] - q.p[3] : w - q.p[0] - q.p[2];
    var ms = [], cs = [], gr = [], fill = [], tot = 0, gs = 0;
    for (j = 0; j < n; j++) {
      var c = k[j], cq = Q[c], s = intr(c);
      var mm = row ? cq.m[0] + cq.m[2] : cq.m[1] + cq.m[3], cm = row ? cq.m[1] + cq.m[3] : cq.m[0] + cq.m[2];
      var gw = (row ? cq.sw : cq.sh) == 1 ? (row ? cq.fw : cq.fh) : 0;
      var cf = cq.st ? (row ? cq.sh : cq.sw) == 1 : A[1] == 3;
      ms.push(row ? s[0] : s[1]); cs.push(cf ? Math.max(0, ic - cm) : (row ? s[1] : s[0]));
      gr.push(gw); fill.push(cf); tot += ms[j] + mm; gs += gw;
    }
    tot += q.gm * (n - 1);
    var free = im - tot;
    if (free > 0 && gs > 0) { for (j = 0; j < n; j++) ms[j] += free * gr[j] / gs; free = 0; }
    var lead = 0, gap = q.gm;
    if (A[0] == 1) lead = free / 2; else if (A[0] == 2) lead = free;
    else if (A[0] == 3 && n > 1 && free > 0) gap += free / (n - 1);
    var pos = lead;
    for (j = 0; j < n; j++) {
      var c = k[j], cq = Q[c];
      var ml = row ? cq.m[0] : cq.m[1], mt = row ? cq.m[2] : cq.m[3], cl = row ? cq.m[1] : cq.m[0], ct = row ? cq.m[3] : cq.m[2];
      if (rev) { var sw = ml; ml = mt; mt = sw; }   // margins are physical: in reverse the leading one is right / bottom
      var mp = pos + ml; pos = mp + ms[j] + mt + gap;
      var fc = ic - cs[j] - cl - ct, ca = fill[j] ? 0 : A[1];
      var cp = cl + (ca == 1 ? fc / 2 : ca == 2 ? fc : 0);
      if (rev) mp = im - mp - ms[j];
      R[c] = [q.p[0] + (row ? mp : cp), q.p[1] + (row ? cp : mp), 0, 0];
      lay(c, row ? ms[j] : cs[j], row ? cs[j] : ms[j]);
    }
  }
  for (i = 0; i < N.length; i++) R.push([0, 0, 0, 0]);
  lay(0, Q[0].st ? Q[0].W : N[0].w, Q[0].st ? Q[0].H : N[0].h);
  return R;
}
"""


def solve(T):
    """the solver run by Node.js (tests, flatten): [[left, top, width, height], ...]"""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node not found")
    prog = SOLVER_JS + f"\nconsole.log(JSON.stringify(rlay({json.dumps(T)}, null)));\n"
    res = subprocess.run([node, "-"], input=prog, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(res.stderr)
    return json.loads(res.stdout)


def flatten(src, dst):
    """A copy of the RML project src in dst where every LayoutComponent is a Node at the place the solver gives it, its
    paints on a rectangle the solved size behind its children. Rendered by the Rive CLI next to the original, it shows
    whether the solver places the boxes as the runtime does (CI)."""
    from .model import Project
    proj = Project(src)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build"))
    rects = []                         # per artboard (Project order), the solved place of each LayoutComponent
    for ab in proj.artboards:
        T, index, why = tree(proj, ab)
        if why and why != "no layout":
            raise ValueError(f"{ab.name}: {why}")
        R = solve(T) if T is not None else []
        rects.append([(R[index[id(e)]], style_of(proj, e)) for e in layout_nodes(ab)])
    abn = 0
    for f in sorted(os.listdir(dst)):
        if not f.endswith(".rml"):
            continue
        path = os.path.join(dst, f)
        tree_ = ET.parse(path)
        root = tree_.getroot()
        parents = {c: p for p in root.iter() for c in p}
        for ab_x in [c for c in root if c.tag == "Artboard"]:
            ab_x.attrib.pop("styleId", None)
            places = rects[abn]
            abn += 1
            lcs = [e for e in ab_x.iter() if e is not ab_x and e.tag == "LayoutComponent"]
            for el, (r, st) in zip(lcs, places):
                el.tag = "Node"
                for k in ("styleId", "width", "height", "clip", "fractionalWidth", "fractionalHeight"):
                    el.attrib.pop(k, None)
                el.set("x", repr(_num(el.get("x")) + r[0]))
                el.set("y", repr(_num(el.get("y")) + r[1]))
                paints = [c for c in el if c.tag in ("Fill", "Stroke")]
                if paints:
                    radii = {k: v for k, v in (st.attrs.items() if st is not None else ())
                             if k.startswith("cornerRadius") or k == "linkCornerRadius"}
                    shp = ET.Element("Shape", {"name": (el.get("name") or "") + " box"})
                    ET.SubElement(shp, "Rectangle", dict({"originX": "0", "originY": "0", "width": repr(r[2]),
                                                          "height": repr(r[3]), "name": "R"}, **radii))
                    for p in paints:
                        el.remove(p)
                        shp.append(p)
                    el.append(shp)              # last child: drawn under the others
            for el, (r, st) in zip(lcs, places):
                if st is None:
                    continue
                for shp in el:
                    if shp.tag != "Shape":
                        continue
                    for pp in shp:
                        if pp.tag in ("Rectangle", "Ellipse", "Triangle", "Polygon", "Star"):
                            pp.set("width", repr(r[2]))
                            pp.set("height", repr(r[3]))
        for el in list(root.iter("LayoutComponentStyle")):
            parents[el].remove(el)
        tree_.write(path, encoding="unicode")
    return [[r for r, _st in a] for a in rects]


if __name__ == "__main__":
    import sys
    print(json.dumps(flatten(sys.argv[1], sys.argv[2])))
