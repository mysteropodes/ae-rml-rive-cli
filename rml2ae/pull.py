"""`ae pull`: what you changed in After Effects goes back into scene.rml.

Scope: the transform of every layer that rml2ae tagged with a Rive id (`comment` = "rive:<id>", inside a comp
tagged "rive:<artboard>|<animation>" or "…|clip") — x, y, rotation, scaleX, scaleY, opacity, hidden flag —
as static attributes when the property has no animation in AE, as keyframes of that animation otherwise
(AE bezier eases are turned back into Rive CubicEaseInterpolator; linear and hold are kept as such).
Static values are read from the artboard's first animation comp only (the element exists once, it is drawn in every
animation comp); keyframes from each animation comp. A single AE key on a property the RML animation does not key
is a value carried from a previous state, not an edit: ignored.
Shape contents (fill / stroke colour, stroke thickness, paths) are read from the same dump: pullshape.py.

The RML is edited in place as text (attributes and <KeyedProperty> blocks only, nothing else moves), then checked
with `rive <project> --verify`; the build manifest is updated so the next incremental build does not rebuild the
layers you just pulled from (their AE effects stay).
"""
import glob
import json
import math
import os
import re
import subprocess

from .jsx import js, TIDY_JSX, TIDY_PULL_JSX
from .model import Project, is_a

PROPS = {"x": 13, "y": 14, "rotation": 15, "scaleX": 16, "scaleY": 17, "opacity": 18}
EPS = {"x": 0.01, "y": 0.01, "rotation": 0.0005, "scaleX": 0.0005, "scaleY": 0.0005, "opacity": 0.002}

DUMP_JSX = r'''
var LOGF = new File(__LOG__); LOGF.open("w"); LOGF.close();
function log(s) { LOGF.open("a"); LOGF.write(s + "\n"); LOGF.close(); }
function q(s) { return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, " ") + '"'; }
function num(v) { return (isNaN(v) || !isFinite(v)) ? "0" : String(Math.round(v * 100000) / 100000); }
function prop(L, name) { return L.property("ADBE Transform Group").property(name); }
function itype(t) { return t == KeyframeInterpolationType.HOLD ? "h" : (t == KeyframeInterpolationType.LINEAR ? "l" : "b"); }
function ease(arr) { var o = []; for (var i = 0; i < arr.length; i++) o.push("[" + num(arr[i].speed) + "," + num(arr[i].influence) + "]"); return "[" + o.join(",") + "]"; }
function pts(a) { var o = []; for (var i = 0; i < a.length; i++) o.push("[" + num(a[i][0]) + "," + num(a[i][1]) + "]"); return "[" + o.join(",") + "]"; }
// a value: number, array (every component: colours have 4) or path (Shape: vertices and tangents in the group's space)
function val(v) {
  if (v instanceof Shape) return "{\"vs\":" + pts(v.vertices) + ",\"in\":" + pts(v.inTangents) + ",\"out\":" + pts(v.outTangents) + ",\"c\":" + (v.closed ? "true" : "false") + "}";
  if (v instanceof Array) { var o = []; for (var i = 0; i < v.length; i++) o.push(num(v[i])); return "[" + o.join(",") + "]"; }
  return num(v);
}
function dumpProp(p) {
  var pv = p.value; try { pv = p.valueAtTime(0, true); } catch (e9) {}     // pre-expression (opacity carries an expression)
  var out = "{\"v\":" + val(pv);
  if (p.numKeys > 0) {
    var ks = [];
    for (var i = 1; i <= p.numKeys; i++) {
      ks.push("{\"t\":" + num(p.keyTime(i)) + ",\"v\":" + val(p.keyValue(i)) +
              ",\"i\":" + q(itype(p.keyInInterpolationType(i))) + ",\"o\":" + q(itype(p.keyOutInterpolationType(i))) +
              ",\"ie\":" + ease(p.keyInTemporalEase(i)) + ",\"oe\":" + ease(p.keyOutTemporalEase(i)) + "}");
    }
    out += ",\"k\":[" + ks.join(",") + "]";
  }
  try { if (p.expressionEnabled && p.expression) out += ",\"x\":true"; } catch (eX) {}     // driven (data binding): not an edit
  return out + "}";
}
// the contents of a vector group, in AE order: paths, fills, strokes (and anything else by match name)
function dumpItems(gc) {
  var out = [];
  for (var i = 1; i <= gc.numProperties; i++) {
    var it = gc.property(i), mn = it.matchName, r = null;
    if (mn == "ADBE Vector Group") continue;
    if (mn == "ADBE Vector Shape - Group") r = "\"m\":\"path\",\"p\":" + dumpProp(it.property("ADBE Vector Shape"));
    else if (mn == "ADBE Vector Shape - Rect") r = "\"m\":\"rect\",\"size\":" + dumpProp(it.property("ADBE Vector Rect Size")) + ",\"pos\":" + dumpProp(it.property("ADBE Vector Rect Position")) + ",\"rd\":" + dumpProp(it.property("ADBE Vector Rect Roundness"));
    else if (mn == "ADBE Vector Shape - Ellipse") r = "\"m\":\"ellipse\",\"size\":" + dumpProp(it.property("ADBE Vector Ellipse Size")) + ",\"pos\":" + dumpProp(it.property("ADBE Vector Ellipse Position"));
    else if (mn == "ADBE Vector Shape - Star") r = "\"m\":\"star\"";
    else if (mn == "ADBE Vector Graphic - Fill") r = "\"m\":\"fill\",\"c\":" + dumpProp(it.property("ADBE Vector Fill Color")) + ",\"o\":" + dumpProp(it.property("ADBE Vector Fill Opacity"));
    else if (mn == "ADBE Vector Graphic - Stroke") r = "\"m\":\"stroke\",\"c\":" + dumpProp(it.property("ADBE Vector Stroke Color")) + ",\"o\":" + dumpProp(it.property("ADBE Vector Stroke Opacity")) + ",\"w\":" + dumpProp(it.property("ADBE Vector Stroke Width")) +
                                                          ",\"cap\":" + dumpProp(it.property("ADBE Vector Stroke Line Cap")) + ",\"join\":" + dumpProp(it.property("ADBE Vector Stroke Line Join"));
    else r = "\"m\":" + q(mn);
    out.push("{" + r + ",\"n\":" + q(it.name) + ",\"on\":" + (it.enabled ? "true" : "false") + "}");
  }
  return "[" + out.join(",") + "]";
}
// vector groups of a shape layer (rml2ae's industry layout: a group per Rive Node / Shape), by rank among groups
function dumpGroups(container, path, out) {
  var k = 0;
  for (var i = 1; i <= container.numProperties; i++) {
    var g = container.property(i);
    if (g.matchName != "ADBE Vector Group") continue;
    k++;
    var p2 = path.concat([k]);
    var t = g.property("ADBE Vector Transform Group");
    out.push("{\"path\":[" + p2.join(",") + "],\"n\":" + q(g.name) + ",\"enabled\":" + (g.enabled ? "true" : "false") + ",\"p\":" + dumpProp(t.property("ADBE Vector Position")) +
             ",\"a\":" + dumpProp(t.property("ADBE Vector Anchor")) +
             ",\"r\":" + dumpProp(t.property("ADBE Vector Rotation")) + ",\"s\":" + dumpProp(t.property("ADBE Vector Scale")) +
             ",\"o\":" + dumpProp(t.property("ADBE Vector Group Opacity")) + ",\"items\":" + dumpItems(g.property("ADBE Vectors Group")) + "}");
    dumpGroups(g.property("ADBE Vectors Group"), p2, out);
  }
}
// a layer added in AE (no rml2ae tag), or a copy of a tagged layer (AE duplicates the comment): read whole for pulladd.py
function layerKind(L) {
  if (L instanceof ShapeLayer) return "shape";
  if (L instanceof TextLayer) return "text";
  if (L instanceof CameraLayer) return "camera";
  if (L instanceof LightLayer) return "light";
  if (L.nullLayer) return "null";
  if (L.adjustmentLayer) return "adjust";
  try { if (L.source instanceof CompItem) return "precomp"; if (L.source.mainSource instanceof SolidSource) return "solid"; } catch (eK) {}
  return "footage";
}
function dumpAdded(L, copy) {
  var k = layerKind(L);
  var sepd = false; try { sepd = prop(L, "ADBE Position").dimensionsSeparated; } catch (e1) {}
  var rec = "{\"lid\":" + L.id + ",\"i\":" + L.index + ",\"par\":" + (L.parent ? L.parent.index : 0) + ",\"name\":" + q(L.name) + ",\"kind\":" + q(k) +
            ",\"copy\":" + (copy ? "true" : "false") + ",\"enabled\":" + (L.enabled ? "true" : "false") + ",\"in\":" + num(L.inPoint) + ",\"out\":" + num(L.outPoint);
  if (k == "camera" || k == "light") return rec + "}";
  var tm = false, hm = false, td = false, nfx = 0, nmk = 0;
  try { tm = L.isTrackMatte; hm = L.hasTrackMatte; td = L.threeDLayer; } catch (e2) {}
  try { nfx = L.property("ADBE Effect Parade").numProperties; nmk = L.property("ADBE Mask Parade").numProperties; } catch (e3) {}
  rec += ",\"isMatte\":" + (tm ? "true" : "false") + ",\"hasMatte\":" + (hm ? "true" : "false") + ",\"threeD\":" + (td ? "true" : "false") +
         ",\"fx\":" + nfx + ",\"masks\":" + nmk + ",\"blend\":" + (L.blendingMode == BlendingMode.NORMAL ? "false" : "true") + ",\"sep\":" + (sepd ? "true" : "false");
  if (sepd) rec += ",\"px\":" + dumpProp(prop(L, "ADBE Position_0")) + ",\"py\":" + dumpProp(prop(L, "ADBE Position_1"));
  else rec += ",\"p\":" + dumpProp(prop(L, "ADBE Position"));
  rec += ",\"a\":" + dumpProp(prop(L, "ADBE Anchor Point")) + ",\"r\":" + dumpProp(prop(L, "ADBE Rotate Z")) + ",\"s\":" + dumpProp(prop(L, "ADBE Scale")) + ",\"o\":" + dumpProp(prop(L, "ADBE Opacity"));
  if (k == "shape") {
    var gs = [], rv = L.property("ADBE Root Vectors Group");
    dumpGroups(rv, [], gs);
    rec += ",\"groups\":[" + gs.join(",") + "],\"items\":" + dumpItems(rv);
  }
  if (k == "solid") { var so = L.source; rec += ",\"solid\":[" + num(so.width) + "," + num(so.height) + "," + val(so.mainSource.color) + "]"; }
  return rec + "}";
}
// a comp rml2ae built (in its "<project> (rml2ae)" folder), not a comp of an original project tagged by ae2rml, whose
// untagged layers are the ones ae2rml did not convert
function inBuild(it) {
  var f = it.parentFolder;
  for (var d = 0; d < 20 && f && f !== app.project.rootFolder; d++) { if (/ \(rml2ae\)$/.test(f.name)) return true; f = f.parentFolder; }
  return false;
}
var OUT = new File(__OUT__);
var T0 = (new Date()).getTime();
var TIDY = {};
for (var ti = 1; ti <= app.project.numItems; ti++) { var tit = app.project.item(ti); if (tit instanceof FolderItem && tit.name == "rml2ae tidy") { var tr0 = tidyLoad(tit); for (var tk in tr0) TIDY[tk] = tr0[tk]; } }
try {
  var comps = [], ITEMS = [];
  // a snapshot: the temporary nulls of tidyWrap add solids to the project, which shifts the item indices
  for (var i0 = 1; i0 <= app.project.numItems; i0++) ITEMS.push(app.project.item(i0));
  for (var i = 0; i < ITEMS.length; i++) {
    var it = ITEMS[i];
    if (!(it instanceof CompItem) || !it.comment || it.comment.indexOf("rive:") != 0) continue;
    var parts = it.comment.split("|");
    if (parts.length < 2 || parts[1] == "seq" || parts[1] == "sm" || parts[1] == "looped" || parts[1] == "stepped") continue;
    if (parts[0].indexOf("+matte") > 0) continue;                    // a LayerMask's matte: a copy of its source
    var layers = [], LL = [], stack = [], added = [], MAIN = {}, COPY = {}, BUILT = inBuild(it);
    for (var j0 = 1; j0 <= it.numLayers; j0++) LL.push(it.layer(j0));   // tidyWrap adds layers: iterate a snapshot
    // the stack as it is (before tidyWrap adds its temporary nulls); a tag carried by two layers: the higher layer id
    // is AE's copy (Duplicate keeps the comment), the original keeps the lowest
    for (var j1 = 0; j1 < LL.length; j1++) { var c1 = LL[j1].comment || ""; if (c1.indexOf("rive:") == 0 && c1.indexOf("+") < 0 && (!(c1 in MAIN) || LL[j1].id < MAIN[c1])) MAIN[c1] = LL[j1].id; }
    for (var j2 = 0; j2 < LL.length; j2++) {
      var L2 = LL[j2], c2 = L2.comment || "", an = [0, 0];
      try { an = prop(L2, "ADBE Anchor Point").valueAtTime(0, true); } catch (eA) {}
      stack.push("{\"i\":" + L2.index + ",\"lid\":" + L2.id + ",\"c\":" + q(c2) + ",\"a\":" + val(an) + "}");
      var cp = c2.indexOf("rive:") == 0 && c2.indexOf("+") < 0 && MAIN[c2] != L2.id;
      if (BUILT && (cp || !/^(rive:|anon:|head|vm:|audio:)/.test(c2))) {
        if (cp) COPY[L2.id] = true;
        try { added.push(dumpAdded(L2, cp)); } catch (eD) { log("added layer " + L2.name + ": " + eD.toString()); }
      }
    }
    for (var j = 0; j < LL.length; j++) {
      var L = LL[j], TW = null;
      var c = L.comment || "";
      if (c.indexOf("rive:") != 0 || COPY[L.id]) continue;
      var isRun = c.indexOf("+run") > 0;
      if (c.indexOf("+") >= 0 && !isRun) continue;
      if (L.name.indexOf("[skinned]") >= 0) continue;
      try { if (L.source && L.source instanceof CompItem && L.source.comment && L.source.comment.indexOf("|clip") > 0) continue; } catch (e0) {}
      TW = tidyWrap(it, L, TIDY);                     // a null dissolved by the build's tidy pass, back for the read
      var sepd = false; try { sepd = prop(L, "ADBE Position").dimensionsSeparated; } catch (e1) {}
      var tm = false; try { tm = L.isTrackMatte; } catch (e2) {}                   // a matte layer is switched off by AE itself
      var rec = "{\"id\":" + q(c.substr(5)) + ",\"name\":" + q(L.name) + ",\"enabled\":" + ((L.enabled || tm) ? "true" : "false") + ",\"sep\":" + (sepd ? "true" : "false");
      if (sepd) rec += ",\"px\":" + dumpProp(prop(L, "ADBE Position_0")) + ",\"py\":" + dumpProp(prop(L, "ADBE Position_1"));
      else rec += ",\"p\":" + dumpProp(prop(L, "ADBE Position"));
      rec += ",\"r\":" + dumpProp(prop(L, "ADBE Rotate Z")) + ",\"s\":" + dumpProp(prop(L, "ADBE Scale")) + ",\"o\":" + dumpProp(prop(L, "ADBE Opacity"));
      rec += ",\"tag\":" + q(c) + ",\"run\":" + (isRun ? "true" : "false")
      if (tm || L.name.indexOf("(matte)") >= 0) rec += ",\"matte\":true";     // a clip's geometry: its paints are not the element's
      var tch = TIDY[it.id + ":" + L.id];
      var top = TIDY["op:" + it.id + ":" + L.id];       // the opacity of a lifted precomp's node, carried by this layer
      if (top && top.length && top[0][0].indexOf("rive:") == 0) rec += ",\"opid\":" + q(top[0][0].substr(5).split("+")[0]);
      if (tch && tch.length && !TW && !tidyRotated(tch)) { var tp = []; for (var ti2 = 0; ti2 < tch.length; ti2++) tp.push("[" + tch[ti2].join(",") + "]"); rec += ",\"untidy\":[" + tp.join(",") + "]"; }
      var lu = [];                                       // script inputs as rml2ae built them ("Luau: <name>" controls)
      try { var EP = L.property("ADBE Effect Parade"); for (var e = 1; e <= EP.numProperties; e++) { var E = EP.property(e); if (E.name.indexOf("Luau: ") != 0) continue;
        var pv = E.property(1), v = pv.value; lu.push("{\"name\":" + q(E.name.substr(6)) + ",\"k\":" + q(E.matchName) + ",\"keyed\":" + (pv.numKeys > 0 ? "true" : "false") + ",\"v\":" + (v instanceof Array ? "[" + num(v[0]) + "," + num(v[1]) + "," + num(v[2]) + "]" : num(v)) + "}"); } } catch (el0) {}
      if (lu.length) rec += ",\"luau\":[" + lu.join(",") + "]";
      var gs = []; try { if (L.property("ADBE Root Vectors Group")) dumpGroups(L.property("ADBE Root Vectors Group"), [], gs); } catch (eg) {}
      rec += ",\"groups\":[" + gs.join(",") + "]}";
      tidyUnwrap(TW);
      layers.push(rec);
    }
    log("read " + it.name + ": " + layers.length + " layer(s), " + ((new Date()).getTime() - T0) + " ms");
    comps.push("{\"tag\":" + q(it.comment) + ",\"id\":" + it.id + ",\"built\":" + (inBuild(it) ? "true" : "false") + ",\"name\":" + q(it.name) + ",\"fps\":" + num(it.frameRate) + ",\"layers\":[" + layers.join(",") + "]" +
               ",\"stack\":[" + stack.join(",") + "],\"added\":[" + added.join(",") + "]}");
  }
  OUT.open("w"); OUT.write("[" + comps.join(",") + "]"); OUT.close();
  log("dumped " + comps.length + " comp(s)");
} catch (e) { log("TOP FAILED: " + e.toString() + " line " + e.line); }
log("DONE");
'''


TAGS_JSX = "_pull_tags.jsx"


def tags_jsx(tags, log):
    """the script that writes `rive:<id>` into the comment of each layer `ae pull` turned into a new element"""
    rows = sorted(set(tags))
    return f'''var LOGF = new File({js(log)}); LOGF.open("w"); LOGF.close();
function log(s) {{ LOGF.open("a"); LOGF.write(s + "\\n"); LOGF.close(); }}
var T = {js([list(r) for r in rows])}, n = 0;
try {{
  for (var i = 1; i <= app.project.numItems; i++) {{
    var it = app.project.item(i); if (!(it instanceof CompItem)) continue;
    for (var k = 0; k < T.length; k++) {{
      if (T[k][0] != it.id) continue;
      for (var j = 1; j <= it.numLayers; j++) if (it.layer(j).id == T[k][1]) {{ it.layer(j).comment = T[k][2]; n++; }}
    }}
  }}
  log(n + " added layer(s) tagged rive:<id>");
}} catch (e) {{ log("TAGS FAILED: " + e.toString() + " line " + e.line); }}
log("DONE");
'''


def fmt(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def keys_to_rive(ks, dim, fps_anim, speed, unit_scale):
    """AE keys (dump records) -> [(frame, value, interp, ease)] in Rive units. `dim` = component of a 2D value, else None."""
    out = []
    n = len(ks)
    for i, k in enumerate(ks):
        v = (k["v"][dim] if dim is not None else k["v"]) * unit_scale
        frame = k["t"] * fps_anim * speed
        if i == n - 1:
            out.append((frame, v, "hold" if k["o"] == "h" else "linear", None))
            continue
        nk = ks[i + 1]
        if k["o"] == "h":
            out.append((frame, v, "hold", None))
            continue
        v1 = (nk["v"][dim] if dim is not None else nk["v"]) * unit_scale
        dv, dt = v1 - v, nk["t"] - k["t"]
        d = min(dim if dim is not None else 0, len(k["oe"]) - 1)
        so, io = k["oe"][d]
        si, ii = nk["ie"][d]
        if k["o"] == "l" and nk["i"] == "l":
            out.append((frame, v, "linear", None))
            continue
        x1 = io / 100.0
        x2 = 1 - ii / 100.0
        if abs(dv) < 1e-9 or dt <= 0:
            out.append((frame, v, "linear", None))
            continue
        y1 = so * unit_scale * x1 * dt / dv
        y2 = 1 - si * unit_scale * (1 - x2) * dt / dv
        # rml2ae writes a vertical Rive handle (x1 = 0 / x2 = 1) as AE's minimum influence 0.1 % (convert.kind_of):
        # read it back as the vertical handle it was (its height y comes from the AE speed above)
        if abs(x1 - 0.001) < 5e-6:
            x1 = 0.0
        if abs(x2 - 0.999) < 5e-6:
            x2 = 1.0
        if abs(x1 - y1) < 0.02 and abs(x2 - y2) < 0.02:          # handles on the diagonal = the identity curve
            out.append((frame, v, "linear", None))
        else:
            out.append((frame, v, "cubic", (round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4))))
    return out


def _value(keys, f):
    """value of a Rive key list at frame f (hold / linear / cubic ease)"""
    if f <= keys[0][0]:
        return keys[0][1]
    for (f0, v0, i0, e0), (f1, v1, _i1, _e1) in zip(keys, keys[1:]):
        if f0 <= f < f1:
            if i0 == "hold":
                return v0
            t = (f - f0) / (f1 - f0)
            if i0 == "cubic" and e0:
                x1, y1, x2, y2 = e0
                lo, hi = 0.0, 1.0
                for _ in range(40):
                    m = (lo + hi) / 2
                    if 3 * (1 - m) ** 2 * m * x1 + 3 * (1 - m) * m * m * x2 + m ** 3 < t:
                        lo = m
                    else:
                        hi = m
                m = (lo + hi) / 2
                t = 3 * (1 - m) ** 2 * m * y1 + 3 * (1 - m) * m * m * y2 + m ** 3
            return v0 + (v1 - v0) * t
    return keys[-1][1]


def same_motion(a, b, eps, static=None):
    """the two key lists (or a list and a static value) give the same value at every frame — rml2ae writes a 2-D
    group position whose x carries constant keys, or bakes x/y frame by frame: not an edit"""
    if not a and not b:
        return True
    a = a or [(0, static, "hold", None)]
    b = b or [(0, static, "hold", None)]
    if any(k[1] is None for k in a + b):
        return False
    f0 = int(min(a[0][0], b[0][0]))
    f1 = int(max(a[-1][0], b[-1][0])) + 1
    tol = eps * 20 if eps < 0.01 else eps * 5
    return all(abs(_value(a, f + h) - _value(b, f + h)) <= tol for f in range(f0, f1 + 1) for h in (0.0, 0.5))


def same_keys(a, b, eps):
    if len(a) != len(b):
        return False
    n = len(a)
    for k, ((fa, va, ia, ea), (fb, vb, ib, eb)) in enumerate(zip(a, b)):
        if k == n - 1:
            ia = ib                    # the last key's interpolation leads nowhere
        if abs(fa - fb) > 0.01 or abs(va - vb) > eps or ia != ib:
            return False
        if ia == "cubic" and any(abs(x - y) > 0.01 for x, y in zip(ea or (0, 0, 0, 0), eb or (0, 0, 0, 0))):
            return False
    return True


def key_xml(frame, v, interp, ease):
    f = int(round(frame))
    if interp == "cubic" and ease:
        return (f'<KeyFrameDouble value="{fmt(v)}" frame="{f}" interpolationType="cubic">'
                f'<CubicEaseInterpolator x1="{fmt(ease[0])}" y1="{fmt(ease[1])}" x2="{fmt(ease[2])}" y2="{fmt(ease[3])}"/></KeyFrameDouble>')
    return f'<KeyFrameDouble value="{fmt(v)}" frame="{f}" interpolationType="{interp}"/>'


class RmlText:
    """Text-level edits of one .rml file (attributes of an element by id, KeyedProperty blocks of an animation)."""

    def __init__(self, path):
        self.path = path
        self.text = open(path, encoding="utf-8").read()
        self.changed = False

    def has_id(self, oid):
        return f'id="{oid}"' in self.text

    def set_attr(self, oid, name, value):
        m = re.search(r'<(\w+)\b([^<>]*?\sid="' + re.escape(oid) + r'"[^<>]*?)(/?)>', self.text)
        if not m:
            return False
        attrs = m.group(2)
        if re.search(r'\s' + name + r'="', attrs):
            if value is None:
                new_attrs = re.sub(r'\s' + name + r'="[^"]*"', "", attrs)
            else:
                new_attrs = re.sub(r'(\s' + name + r'=")[^"]*(")', lambda mm: mm.group(1) + value + mm.group(2), attrs)
        else:
            if value is None:
                return False
            new_attrs = f' {name}="{value}"' + attrs
        self.text = self.text[:m.start(2)] + new_attrs + self.text[m.end(2):]
        self.changed = True
        return True

    def set_keys(self, anim_id, oid, pk, keys, kxml=None):
        pat = r'<LinearAnimation\b[^<>]*?\sid="' + re.escape(anim_id) + r'"[^<>]*>'
        m = re.search(pat, self.text)
        if not m:
            return False
        if self.text[m.start():m.end()].endswith("/>"):
            if not keys:
                return False
            # an animation with no key yet is written self-closing: open it before adding the first keys
            opened = self.text[m.start():m.end() - 2].rstrip() + "></LinearAnimation>"
            self.text = self.text[:m.start()] + opened + self.text[m.end():]
            m = re.search(pat, self.text)
        end = self.text.index("</LinearAnimation>", m.end())
        block = self.text[m.end():end]
        body = "".join((kxml or key_xml)(*k) for k in keys)
        kp = f'<KeyedProperty propertyKey="{pk}">{body}</KeyedProperty>' if keys else ""
        ko = re.search(r'<KeyedObject objectId="' + re.escape(oid) + r'">', block)
        if ko is None:
            if not keys:
                return False
            block = block.rstrip() + f'\n<KeyedObject objectId="{oid}">{kp}</KeyedObject>\n'
        else:
            ko_end = block.index("</KeyedObject>", ko.end())
            inner = block[ko.end():ko_end]
            pm = re.search(r'<KeyedProperty propertyKey="' + str(pk) + r'">.*?</KeyedProperty>', inner, re.S)
            inner = inner[:pm.start()] + kp + inner[pm.end():] if pm else inner + kp
            if inner.strip():
                block = block[:ko.end()] + inner + block[ko_end:]
            else:                                   # the object has no keyed property left: drop it
                block = block[:ko.start()] + block[ko_end + len("</KeyedObject>"):]
        self.text = self.text[:m.end()] + block + self.text[end:]
        self.changed = True
        return True

    # ---- elements without an id (a Fill, its SolidColor, a path, its vertices): addressed from their nearest ancestor
    # that has one, by rank among element children (the model's El tree has the same children, comments aside)
    _TAG = re.compile(r'<!--.*?-->|<\?.*?\?>|<(/?)([A-Za-z_][\w:.-]*)((?:[^<>"]|"[^"]*")*?)(/?)>', re.S)

    def _element(self, pos):
        """the element whose start tag is at `pos` -> (start, start-tag end, end, self-closing)"""
        m = self._TAG.match(self.text, pos)
        if m.group(4):
            return (pos, m.end(), m.end(), True)
        depth = 0
        for t in self._TAG.finditer(self.text, m.end()):
            if t.group(2) is None:
                continue
            if t.group(1):
                if depth == 0:
                    return (pos, m.end(), t.end(), False)
                depth -= 1
            elif not t.group(4):
                depth += 1
        raise ValueError("unclosed element at %d" % pos)

    def _children(self, span):
        out = []
        if span[3]:
            return out
        pos = span[1]
        while True:
            t = self._TAG.search(self.text, pos)
            if t is None or t.group(1):
                return out
            if t.group(2) is None:
                pos = t.end()
                continue
            c = self._element(t.start())
            out.append(c)
            pos = c[2]

    def span(self, el):
        """(start, start-tag end, end, self-closing) of a model element in this text, or None"""
        chain = []
        while el is not None and not el.id:
            if el.parent is None:
                return None
            chain.append(el.parent.children.index(el))
            el = el.parent
        if el is None:
            return None
        m = re.search(r'<\w+\b[^<>]*?\sid="' + re.escape(el.id) + r'"', self.text)
        if not m:
            return None
        sp = self._element(m.start())
        for k in reversed(chain):
            kids = self._children(sp)
            if k >= len(kids):
                return None
            sp = kids[k]
        return sp

    def set_attr_el(self, el, name, value):
        """set (or drop, value None) one attribute of a model element, with or without an id; the model follows"""
        sp = self.span(el)
        if sp is None:
            return False
        head = self.text[sp[0]:sp[1]]
        m = re.match(r'<[\w:.-]+', head)
        tail = "/>" if sp[3] else ">"
        attrs = head[m.end():len(head) - len(tail)]
        if re.search(r'\s' + name + r'="', attrs):
            if value is None:
                attrs = re.sub(r'\s' + name + r'="[^"]*"', "", attrs)
            else:
                attrs = re.sub(r'(\s' + name + r'=")[^"]*(")', lambda mm: mm.group(1) + value + mm.group(2), attrs)
        elif value is not None:
            attrs = attrs.rstrip() + f' {name}="{value}"'
        else:
            return False
        self.text = self.text[:sp[0]] + m.group(0) + attrs + tail + self.text[sp[1]:]
        if value is None:
            el.attrs.pop(name, None)
        else:
            el.attrs[name] = value
        self.changed = True
        return True

    def replace_children(self, el, xml):
        """the element's children (and text) replaced by `xml`"""
        sp = self.span(el)
        if sp is None:
            return False
        if sp[3]:
            head = self.text[sp[0]:sp[1] - 2].rstrip()
            tag = re.match(r'<([\w:.-]+)', head).group(1)
            self.text = self.text[:sp[0]] + head + ">" + xml + f"</{tag}>" + self.text[sp[2]:]
        else:
            close = self.text.rindex("</", sp[1], sp[2])
            self.text = self.text[:sp[1]] + xml + self.text[close:]
        self.changed = True
        return True

    def save(self):
        if self.changed:
            open(self.path, "w", encoding="utf-8").write(self.text)


def untidy(L):
    """a layer whose static parent nulls the build's tidy pass dissolved (no rotation in the chain): its dumped values
    back in the space of its Rive parent. Each dissolved null maps a point q to (q - anchor) * scale + position, the
    outermost first; the child's scale was multiplied by theirs."""
    chain = L.pop("untidy")

    def pt(x, y):
        for ax, ay, px, py, _r, sx, sy in chain:                  # outermost first: undo it first
            x = (x - px) * 100.0 / (sx or 1e-9) + ax
            y = (y - py) * 100.0 / (sy or 1e-9) + ay
        return x, y
    fx = fy = 1.0
    for _ax, _ay, _px, _py, _r, sx, sy in chain:
        fx *= sx / 100.0
        fy *= sy / 100.0

    def each(rec, fn):
        if rec is None:
            return
        rec["v"] = fn(rec["v"])
        for k in rec.get("k") or []:
            k["v"] = fn(k["v"])
    if L.get("sep"):
        # x and y are independent without rotation: x' = f(x) only
        each(L.get("px"), lambda v: pt(v, 0.0)[0])
        each(L.get("py"), lambda v: pt(0.0, v)[1])
    else:
        each(L.get("p"), lambda v: list(pt(v[0], v[1])))
    each(L.get("s"), lambda v: [v[0] / (fx or 1e-9), v[1] / (fy or 1e-9)])


def prepare(out_dir):
    """Write the dump script; returns (jsx path, json path, log path)."""
    dump_jsx = os.path.join(out_dir, "_pull.jsx")
    dump_json = os.path.join(out_dir, "_pull.json")
    log = os.path.join(out_dir, "_pull.log")
    if os.path.exists(os.path.join(out_dir, TAGS_JSX)):
        os.remove(os.path.join(out_dir, TAGS_JSX))     # the tags of a previous pull: already run (or stale)
    open(dump_jsx, "w", encoding="utf-8").write((TIDY_JSX + TIDY_PULL_JSX + DUMP_JSX).replace("__LOG__", js(log)).replace("__OUT__", js(dump_json)))
    return dump_jsx, dump_json, log


def split_chains(comps, chains, proj):
    """a state chain built as ONE comp (audit F13: keys shifted by each state's start) -> one virtual comp per state:
    its keys in [start, end) moved back to the state's own time, so the per-animation pull below applies unchanged"""
    import copy
    out = []
    for comp in comps:
        parts = comp["tag"].split("|")
        wins = chains.get(parts[1]) if len(parts) > 1 else None
        if not wins:
            out.append(comp)
            continue
        fps = comp.get("fps") or 25
        for aid, f0, f1 in wins:
            anim = proj.animations.get(aid)
            afps = (anim.fps if anim else fps) or fps
            t0, t1 = f0 / afps, f1 / afps
            last = aid == wins[-1][0]

            def cut(o):
                if isinstance(o, dict):
                    o = {k: cut(v) for k, v in o.items()}
                    if isinstance(o.get("k"), list):
                        ks = [k for k in o["k"] if isinstance(k, dict) and "t" in k
                              and t0 - 1e-6 <= k["t"] < (t1 + 1e-6 if last else t1 - 1e-6)]
                        o["k"] = [dict(k, t=k["t"] - t0) for k in ks]
                    return o
                if isinstance(o, list):
                    return [cut(v) for v in o]
                return o
            v = copy.deepcopy(comp)
            v["layers"] = cut(v["layers"])
            v["added"] = cut(v.get("added") or [])
            v["tag"] = "|".join([parts[0], aid] + parts[2:])
            v["realtag"] = comp["tag"]
            v["window"] = (t0, t1)
            out.append(v)
    return out


def script_inputs(proj, comps, file_of, changes):
    """the 'Luau: <name>' controls of a scripted element's layer -> its ScriptInput propertyValue (static values; a
    keyed control is left to the RML animation). The element is not marked pulled: the next build replays it."""
    from .convert import script_color
    seen = set()
    for comp in comps:
        for L in comp["layers"]:
            el = proj.by_id.get(L.get("id"))
            if el is None or not L.get("luau") or el.id in seen:
                continue
            seen.add(el.id)
            f = file_of(el.id)
            if f is None:
                continue
            for c in L["luau"]:
                inp = next((x for x in el.children if x.tag.startswith("ScriptInput") and x.name == c["name"]), None)
                if inp is None or c.get("keyed"):
                    continue
                old = inp.get("propertyValue")
                if inp.tag == "ScriptInputNumber":
                    if abs(float(c["v"]) - inp.num("propertyValue", 0)) <= 1e-4:
                        continue
                    new = fmt(float(c["v"]))
                elif inp.tag == "ScriptInputBoolean":
                    new = "true" if float(c["v"]) > 0.5 else "false"
                    if new == (old or "false"):
                        continue
                elif inp.tag == "ScriptInputColor":
                    r0, g0, b0, a0 = script_color(old)
                    r, g, b = (max(0.0, min(1.0, float(x))) for x in c["v"][:3])
                    if max(abs(r - r0), abs(g - g0), abs(b - b0)) < 0.5 / 255:
                        continue
                    a8, rgb = int(round(a0 * 255)), [int(round(x * 255)) for x in (r, g, b)]
                    if (old or "").strip().lstrip("-").isdigit():
                        new = str((a8 << 24 | rgb[0] << 16 | rgb[1] << 8 | rgb[2]) - (1 << 32 if a8 >= 128 else 0))
                    else:
                        new = "%02X%02X%02X%02X" % (a8, rgb[0], rgb[1], rgb[2])
                else:
                    continue
                f.set_attr_el(inp, "propertyValue", new)
                changes.append(f"{el.name} ({el.id}) script input {c['name']}: {old} -> {new}")


def pull(project_dir, out_dir, run, dry=False, only_comp=None):
    """Read the open AE project (through `run(jsx, log)`), write the differences into the RML. Returns the change lines."""
    dump_jsx, dump_json, log = prepare(out_dir)
    txt = run(dump_jsx, log, timeout=300)
    if "DONE" not in txt or "FAILED" in txt:
        raise SystemExit("AE dump failed: " + txt[-300:])
    changes = apply(project_dir, out_dir, dry, only_comp)
    tags = os.path.join(out_dir, TAGS_JSX)
    if not dry and os.path.exists(tags):
        t = run(tags, os.path.join(out_dir, "_pull_tags.log"), timeout=120)
        ok = "DONE" in t and "FAILED" not in t
        changes.append(("AE: " + t.strip().splitlines()[0]) if ok else "WARNING the added layers were not tagged in AE: " + t[-200:])
        os.remove(tags)
    return changes


def apply(project_dir, out_dir, dry=False, only_comp=None):
    """Apply an existing dump (build/rml2ae/_pull.json) to the RML. Returns the change lines."""
    proj = Project(project_dir)
    dump_json = os.path.join(out_dir, "_pull.json")
    if not os.path.exists(dump_json):
        raise SystemExit("no dump: run the pull script in After Effects first")
    comps = json.load(open(dump_json, encoding="utf-8"))
    files = {p: RmlText(p) for p in sorted(glob.glob(os.path.join(project_dir, "*.rml")))}

    def file_of(oid):
        for f in files.values():
            if f.has_id(oid):
                return f
        return None

    changes = []
    pulled_ids = set()
    gpath = os.path.join(out_dir, proj.name + ".groups.json")
    side = json.load(open(gpath, encoding="utf-8")) if os.path.exists(gpath) else {}
    group_ids = {tag: {tuple(pth): oid for pth, oid in lst} for tag, lst in (side.get("groups") or {}).items()}
    folds = side.get("folds") or {}
    chains = side.get("chains") or {}
    offsets = side.get("offsets") or {}           # translation of dissolved nodes carried by a layer (audit F14)
    if chains:
        comps = split_chains(comps, chains, proj)
    from .pullshape import Contents
    from .pulladd import Adder, align_groups
    top = [max([0] + [int(n) for f in files.values() for n in re.findall(r'\sid="0:(\d+)"', f.text)])]

    def new_id():                                 # an element keyed for the first time needs an id
        top[0] += 1
        return f"0:{top[0]}"
    adder = Adder(proj, file_of, new_id, changes, pulled_ids, dry)
    for ab in proj.artboards:
        chain, _, _ = proj.entry_chain(ab)
        first_anim = chain[0].id if chain else None
        # static values are read in the first comp (chain order) that holds the element: an element rml2ae did not
        # build in a comp where it is invisible (audit F2) is read where it shows
        order = {a.id: k for k, a in enumerate(chain)}
        first_with = {}
        for comp in comps:
            parts = comp["tag"].split("|")
            if parts[1] not in order:
                continue
            for L in comp["layers"]:
                oid = L.get("id")
                if oid and (oid not in first_with or order[parts[1]] < order[first_with[oid]]):
                    first_with[oid] = parts[1]
        for comp in comps:
            parts = comp["tag"].split("|")
            clip = len(parts) == 3 and parts[2] == "clip"
            anim_id = parts[1]
            anim = proj.animations.get(anim_id)
            if anim is None or anim.el.parent is not ab:
                continue
            if only_comp and comp["name"] != only_comp:
                continue
            static_ok = anim_id == first_anim
            records = []
            for L in comp["layers"]:
                if L.get("untidy"):
                    untidy(L)
                if L.get("opid"):
                    # its opacity is the clipped node's (the precomp it came out of carried it): read for that node,
                    # the layer's own opacity is a plain 100 %
                    records.append({"id": L["opid"], "sep": False, "only": "opacity", "o": L["o"], "name": L.get("name", ""),
                                    "enabled": True})
                    L["o"] = {"v": 100}
                if not L.get("run"):
                    records.append(L)
                gids = align_groups(L, group_ids.get(L.get("tag") or ("rive:" + L["id"]), {}), proj)[0]
                for g in L.get("groups") or []:
                    gid = gids.get(tuple(g["path"]))
                    if gid:
                        records.append(dict(g, id=gid, sep=False, name=L.get("name", ""), group=True))
            for L in records:
                oid = L["id"]
                static_ok = first_with.get(oid, first_anim) == anim_id
                el = proj.by_id.get(oid)
                if el is None or (not clip and el.parent is None):
                    continue
                f = file_of(oid)
                if f is None:
                    continue
                if el.find("Mesh") is not None:
                    continue                           # mesh rows: identity layers, the shape lives in their Corner Pins
                is_bone = el.tag == "Bone"
                # AE property -> (Rive prop, unit scale, dump record, component)
                if L.get("only") == "opacity":
                    plan = []
                elif L["sep"]:
                    plan = [("x", 1.0, L["px"], None), ("y", 1.0, L["py"], None)]
                else:
                    plan = [("x", 1.0, L["p"], 0), ("y", 1.0, L["p"], 1)]
                if L.get("only") == "opacity":
                    plan = [("opacity", 0.01, L["o"], None)]
                else:
                    plan += [("rotation", math.pi / 180, L["r"], None), ("scaleX", 0.01, L["s"], 0), ("scaleY", 0.01, L["s"], 1),
                             ("opacity", 0.01, L["o"], None)]
                for prop, unit, rec, dim in plan:
                    if is_bone and prop in ("x", "y"):
                        continue                       # a Bone sits at its parent's tip: not its own attribute
                    if prop == "opacity" and oid in folds:
                        unit = unit / folds[oid]       # a folded node's layer shows node × leaf opacity
                    ks = rec.get("k") or []
                    off = (offsets.get(oid) or [0.0, 0.0])[0 if prop == "x" else 1] if prop in ("x", "y") else 0.0
                    if off:
                        ks = [dict(k, v=(list(k["v"][:dim]) + [k["v"][dim] - off] + list(k["v"][dim + 1:])) if dim is not None
                                   else k["v"] - off) for k in ks]
                    rml_keys = anim.keys.get((oid, PROPS[prop]))
                    if len(ks) >= 2 or (ks and rml_keys):
                        new = keys_to_rive(ks, dim, anim.fps, anim.speed, unit)
                        if comp.get("window") and rml_keys and rml_keys[-1][0] >= anim.duration - 0.01 \
                                and (not new or new[-1][0] < anim.duration - 0.01):
                            new.append(rml_keys[-1])   # its end key shares the frame with the next state's first key
                        old = [(fr, v, i, e) for fr, v, i, e in (rml_keys or [])]
                        default = 1.0 if prop in ("scaleX", "scaleY", "opacity") else 0.0
                        if not same_keys(new, old, EPS[prop]) and not same_motion(new, old, EPS[prop], el.num(prop, default)):
                            if not dry:
                                f.set_keys(anim_id, oid, PROPS[prop], new)
                            changes.append(f"{ab.name} / {anim.name}: {el.name} ({oid}) {prop}: {len(new)} key(s) {'replaced' if old else 'added'}")
                            pulled_ids.add(oid)
                    elif not ks and rml_keys and static_ok and not same_motion(
                            [(0, (rec["v"][dim] if dim is not None else rec["v"]) * unit - off, "hold", None)], rml_keys, EPS[prop]):
                        # (a single constant key is written to AE as a plain value: not a removal)
                        # keys removed in AE (the property is now static there): the RML animation loses them too
                        if not dry:
                            f.set_keys(anim_id, oid, PROPS[prop], [])
                        changes.append(f"{ab.name} / {anim.name}: {el.name} ({oid}) {prop}: keys removed")
                        pulled_ids.add(oid)
                    if not ks and static_ok:
                        cur = (rec["v"][dim] if dim is not None else rec["v"]) * unit - off
                        default = 1.0 if prop in ("scaleX", "scaleY", "opacity") else 0.0
                        was = el.num(prop, default)
                        if abs(cur - was) > EPS[prop]:
                            if not dry:
                                f.set_attr(oid, prop, fmt(cur) if abs(cur - default) > 1e-9 or el.get(prop) is not None else None)
                            changes.append(f"{ab.name}: {el.name} ({oid}) {prop} {fmt(was)} -> {fmt(cur)}")
                            pulled_ids.add(oid)
                if static_ok and not clip:
                    flags = int(el.num("drawableFlags", 0))
                    hidden = bool(flags & 1)
                    if hidden == L["enabled"] and el.tag != "Node":
                        nf = (flags & ~1) | (0 if L["enabled"] else 1)
                        if not dry:
                            f.set_attr(oid, "drawableFlags", str(nf) if nf else None)
                        changes.append(f"{ab.name}: {el.name} ({oid}) {'shown' if L['enabled'] else 'hidden'}")
                        pulled_ids.add(oid)
            # shape contents: fill / stroke colour, stroke thickness, paths (pullshape.py)
            # additions only in the comps rml2ae built (an original project tagged by ae2rml has layers it never converted)
            built = comp.get("built", True)
            cont = Contents(ab, anim, [a for a in proj.animations.values() if a.el.parent is ab], file_of, new_id,
                            changes, pulled_ids, dry, adder if built else None)
            done = set()
            for L in comp["layers"]:
                if L.get("matte"):
                    continue
                gids, unsure = align_groups(L, group_ids.get(L.get("tag") or ("rive:" + L["id"]), {}), proj)
                if built and not clip:
                    adder.groups(ab, anim, L, gids, unsure)      # vector groups added in AE (pulladd.py)
                if L.get("run"):
                    continue
                found = []
                el = proj.by_id.get(L.get("id"))
                if el is not None and is_a(el.tag, "Shape") and not gids:
                    found += [(el, g, True) for g in L.get("groups") or [] if g["path"] == [1]]
                for g in L.get("groups") or []:
                    gel = proj.by_id.get(gids.get(tuple(g["path"])))
                    if gel is not None and is_a(gel.tag, "Shape"):
                        found.append((gel, g, False))
                for sh, g, dedicated in found:
                    if sh.id in done or "items" not in g:
                        continue
                    done.add(sh.id)
                    cont.shape(sh, g["items"], dedicated, first_with.get(L["id"], first_anim) == anim_id, bool(comp.get("window")))
            # layers added in AE (pulladd.py)
            adder.comp(ab, anim, comp, first_anim, len([a for a in proj.animations.values() if a.el.parent is ab]) > 1)
    adder.flush()
    script_inputs(proj, comps, file_of, changes)
    if dry or not changes:
        return changes
    for f in files.values():
        f.save()
    try:
        v = subprocess.run(["rive", project_dir, "--verify"], capture_output=True, text=True)
        if v.returncode != 0:
            changes.append("WARNING rive --verify failed after the pull:\n" + (v.stdout + v.stderr)[-800:])
    except FileNotFoundError:
        changes.append("WARNING rive not found: the pulled RML was not verified")
    # the manifest now describes what AE already has for these elements: no rebuild of them at the next incremental build
    mpath = os.path.join(out_dir, proj.name + ".manifest.json")
    if os.path.exists(mpath):
        from .convert import Converter
        conv = Converter(Project(project_dir), out_dir, keep_project=True, replay=False, replace=True)
        conv.incremental_mode = True
        conv.convert()
        old = json.load(open(mpath, encoding="utf-8"))
        n = 0
        for k, h in conv.manifest.items():
            if any(("#rive:" + i) == k[k.index("#"):] if "#" in k else k.startswith("rive:" + i + "|") for i in pulled_ids):
                if old.get(k) != h:
                    old[k] = h
                    n += 1
        # a layer added in AE and tagged with its new element's id: the next build replaces it by what the RML says (its
        # own block rebuilt in place, or removed when the element went into another layer: a run, a grouped node)
        for ctag, tag in adder.placed:
            old[f"{ctag}#{tag}"] = "pulled"
        json.dump(old, open(mpath, "w", encoding="utf-8"))
        changes.append(f"manifest: {n} block(s) marked up to date (no rebuild of the pulled layers at the next build)")
    if adder.tags:
        # the layers added in AE now stand for their new elements: the next pull reads them as built layers, the next
        # build replaces them in place by what the RML says
        open(os.path.join(out_dir, TAGS_JSX), "w", encoding="utf-8").write(tags_jsx(adder.tags, os.path.join(out_dir, "_pull_tags.log")))
    return changes
