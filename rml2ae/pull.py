"""`ae pull`: what you changed in After Effects goes back into scene.rml.

Scope (v1): the transform of every layer that rml2ae tagged with a Rive id (`comment` = "rive:<id>", inside a comp
tagged "rive:<artboard>|<animation>" or "…|clip") — x, y, rotation, scaleX, scaleY, opacity, hidden flag —
as static attributes when the property has no animation in AE, as keyframes of that animation otherwise
(AE bezier eases are turned back into Rive CubicEaseInterpolator; linear and hold are kept as such).
Static values are read from the artboard's first animation comp only (the element exists once, it is drawn in every
animation comp); keyframes from each animation comp. A single AE key on a property the RML animation does not key
is a value carried from a previous state, not an edit: ignored.

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

from .jsx import js
from .model import Project

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
function dumpProp(p) {
  var pv = p.value; try { pv = p.valueAtTime(0, true); } catch (e9) {}     // pre-expression (opacity carries an expression)
  var out = "{\"v\":" + (pv instanceof Array ? "[" + num(pv[0]) + "," + num(pv[1]) + "]" : num(pv));
  if (p.numKeys > 0) {
    var ks = [];
    for (var i = 1; i <= p.numKeys; i++) {
      var v = p.keyValue(i);
      ks.push("{\"t\":" + num(p.keyTime(i)) + ",\"v\":" + (v instanceof Array ? "[" + num(v[0]) + "," + num(v[1]) + "]" : num(v)) +
              ",\"i\":" + q(itype(p.keyInInterpolationType(i))) + ",\"o\":" + q(itype(p.keyOutInterpolationType(i))) +
              ",\"ie\":" + ease(p.keyInTemporalEase(i)) + ",\"oe\":" + ease(p.keyOutTemporalEase(i)) + "}");
    }
    out += ",\"k\":[" + ks.join(",") + "]";
  }
  return out + "}";
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
    out.push("{\"path\":[" + p2.join(",") + "],\"enabled\":" + (g.enabled ? "true" : "false") + ",\"p\":" + dumpProp(t.property("ADBE Vector Position")) +
             ",\"r\":" + dumpProp(t.property("ADBE Vector Rotation")) + ",\"s\":" + dumpProp(t.property("ADBE Vector Scale")) +
             ",\"o\":" + dumpProp(t.property("ADBE Vector Group Opacity")) + "}");
    dumpGroups(g.property("ADBE Vectors Group"), p2, out);
  }
}
var OUT = new File(__OUT__);
try {
  var comps = [];
  for (var i = 1; i <= app.project.numItems; i++) {
    var it = app.project.item(i);
    if (!(it instanceof CompItem) || !it.comment || it.comment.indexOf("rive:") != 0) continue;
    var parts = it.comment.split("|");
    if (parts.length < 2 || parts[1] == "seq" || parts[1] == "looped" || parts[1] == "stepped") continue;
    var layers = [];
    for (var j = 1; j <= it.numLayers; j++) {
      var L = it.layer(j);
      var c = L.comment || "";
      if (c.indexOf("rive:") != 0) continue;
      var isRun = c.indexOf("+run") > 0;
      if (c.indexOf("+") >= 0 && !isRun) continue;
      if (L.name.indexOf("[skinned]") >= 0) continue;
      try { if (L.source && L.source instanceof CompItem && L.source.comment && L.source.comment.indexOf("|clip") > 0) continue; } catch (e0) {}
      var sepd = false; try { sepd = prop(L, "ADBE Position").dimensionsSeparated; } catch (e1) {}
      var tm = false; try { tm = L.isTrackMatte; } catch (e2) {}                   // a matte layer is switched off by AE itself
      var rec = "{\"id\":" + q(c.substr(5)) + ",\"name\":" + q(L.name) + ",\"enabled\":" + ((L.enabled || tm) ? "true" : "false") + ",\"sep\":" + (sepd ? "true" : "false");
      if (sepd) rec += ",\"px\":" + dumpProp(prop(L, "ADBE Position_0")) + ",\"py\":" + dumpProp(prop(L, "ADBE Position_1"));
      else rec += ",\"p\":" + dumpProp(prop(L, "ADBE Position"));
      rec += ",\"r\":" + dumpProp(prop(L, "ADBE Rotate Z")) + ",\"s\":" + dumpProp(prop(L, "ADBE Scale")) + ",\"o\":" + dumpProp(prop(L, "ADBE Opacity"));
      rec += ",\"tag\":" + q(c) + ",\"run\":" + (isRun ? "true" : "false");
      var gs = []; try { if (L.property("ADBE Root Vectors Group")) dumpGroups(L.property("ADBE Root Vectors Group"), [], gs); } catch (eg) {}
      rec += ",\"groups\":[" + gs.join(",") + "]}";
      layers.push(rec);
    }
    comps.push("{\"tag\":" + q(it.comment) + ",\"name\":" + q(it.name) + ",\"fps\":" + num(it.frameRate) + ",\"layers\":[" + layers.join(",") + "]}");
  }
  OUT.open("w"); OUT.write("[" + comps.join(",") + "]"); OUT.close();
  log("dumped " + comps.length + " comp(s)");
} catch (e) { log("TOP FAILED: " + e.toString() + " line " + e.line); }
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

    def set_keys(self, anim_id, oid, pk, keys):
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
        body = "".join(key_xml(*k) for k in keys)
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

    def save(self):
        if self.changed:
            open(self.path, "w", encoding="utf-8").write(self.text)


def prepare(out_dir):
    """Write the dump script; returns (jsx path, json path, log path)."""
    dump_jsx = os.path.join(out_dir, "_pull.jsx")
    dump_json = os.path.join(out_dir, "_pull.json")
    log = os.path.join(out_dir, "_pull.log")
    open(dump_jsx, "w").write(DUMP_JSX.replace("__LOG__", js(log)).replace("__OUT__", js(dump_json)))
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
            v["tag"] = "|".join([parts[0], aid] + parts[2:])
            v["window"] = (t0, t1)
            out.append(v)
    return out


def pull(project_dir, out_dir, run, dry=False, only_comp=None):
    """Read the open AE project (through `run(jsx, log)`), write the differences into the RML. Returns the change lines."""
    dump_jsx, dump_json, log = prepare(out_dir)
    txt = run(dump_jsx, log, timeout=300)
    if "DONE" not in txt or "FAILED" in txt:
        raise SystemExit("AE dump failed: " + txt[-300:])
    return apply(project_dir, out_dir, dry, only_comp)


def apply(project_dir, out_dir, dry=False, only_comp=None):
    """Apply an existing dump (build/rml2ae/_pull.json) to the RML. Returns the change lines."""
    proj = Project(project_dir)
    dump_json = os.path.join(out_dir, "_pull.json")
    if not os.path.exists(dump_json):
        raise SystemExit("no dump: run the pull script in After Effects first")
    comps = json.load(open(dump_json))
    files = {p: RmlText(p) for p in sorted(glob.glob(os.path.join(project_dir, "*.rml")))}

    def file_of(oid):
        for f in files.values():
            if f.has_id(oid):
                return f
        return None

    changes = []
    pulled_ids = set()
    gpath = os.path.join(out_dir, proj.name + ".groups.json")
    side = json.load(open(gpath)) if os.path.exists(gpath) else {}
    group_ids = {tag: {tuple(pth): oid for pth, oid in lst} for tag, lst in (side.get("groups") or {}).items()}
    folds = side.get("folds") or {}
    chains = side.get("chains") or {}
    offsets = side.get("offsets") or {}           # translation of dissolved nodes carried by a layer (audit F14)
    if chains:
        comps = split_chains(comps, chains, proj)
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
                if not L.get("run"):
                    records.append(L)
                gids = group_ids.get(L.get("tag") or ("rive:" + L["id"]), {})
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
                if L["sep"]:
                    plan = [("x", 1.0, L["px"], None), ("y", 1.0, L["py"], None)]
                else:
                    plan = [("x", 1.0, L["p"], 0), ("y", 1.0, L["p"], 1)]
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
    if dry or not changes:
        return changes
    for f in files.values():
        f.save()
    v = subprocess.run(["rive", project_dir, "--verify"], capture_output=True, text=True)
    if v.returncode != 0:
        changes.append("WARNING rive --verify failed after the pull:\n" + (v.stdout + v.stderr)[-800:])
    # the manifest now describes what AE already has for these elements: no rebuild of them at the next incremental build
    mpath = os.path.join(out_dir, proj.name + ".manifest.json")
    if os.path.exists(mpath):
        from .convert import Converter
        conv = Converter(Project(project_dir), out_dir, keep_project=True, replay=False, replace=True)
        conv.incremental_mode = True
        conv.convert()
        old = json.load(open(mpath))
        n = 0
        for k, h in conv.manifest.items():
            if any(("#rive:" + i) == k[k.index("#"):] if "#" in k else k.startswith("rive:" + i + "|") for i in pulled_ids):
                if old.get(k) != h:
                    old[k] = h
                    n += 1
        json.dump(old, open(mpath, "w"))
        changes.append(f"manifest: {n} block(s) marked up to date (no rebuild of the pulled layers at the next build)")
    return changes
