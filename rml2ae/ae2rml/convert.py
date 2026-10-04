"""After Effects project (.aep) -> Rive CLI project (rive.yaml + scene.rml + assets).

Read with py-aep (no After Effects needed). One artboard per composition reached from the main one; a precomp layer
is a NestedArtboard driven by NestedRemapAnimation.time, so the time mapping (start time, stretch, time remap) is exact.

Layer structure in the RML (only the nodes that are needed):
    Node <layer>                 position / rotation / scale      <- the node `ae pull` maps back to the AE layer
      Node <layer> · anchor      -anchor
        [child layers above it]
        Node <layer> · matte     ClippingShape -> the matte layer's geometry
          Node <Transform fx>    the Transform effect(s)
            Node <layer> · content   opacity, in/out window, ClippingShape(s) -> masks
              drawables
            Node <layer> · masks     mask geometry (no paint)
        [child layers below it]
Draw order is exactly AE's even with parenting: a parent whose children are not contiguous in the layer stack is
repeated ("ghost" nodes carrying the same keys) instead of breaking the order.
"""
import colorsys
import json
import math
import os
import re
import shutil
import subprocess

from . import aexpr, geom, three
from .aexpr import Engine, ShapeValue, tonum, unwrap, to_js, pseudo_param_names, param_defaults
from .keys import AProp, Timeline, progress_keys, reduce_track
from .rml import Anim, E, document, prop_key
from .shapes import PathSource, ShapeBuilder
from .util import IdPool, Report, argb, clean, copy_font, find_footage, fmt, font_metrics, slug

AE_BOX_BASELINE = 0.743     # measured by rml2ae (Montserrat, AE 2026): first baseline of a box text = top + 0.743*size
CONTROL_FX = {"ADBE Slider Control", "ADBE Color Control", "ADBE Checkbox Control", "ADBE Angle Control",
              "ADBE Point Control", "ADBE Point3D Control", "ADBE Layer Control", "ADBE Dropdown Control"}
BLUR_FX = {"ADBE Gaussian Blur 2", "ADBE Gaussian Blur", "ADBE Box Blur2", "ADBE Box Blur"}
COLOR_FX = {"ADBE Fill", "ADBE Tint", "ADBE Invert", "ADBE Exposure2", "ADBE Easy Levels2",
            "ADBE Color Balance (HLS)", "ADBE Brightness & Contrast 2", "ADBE HUE SATURATION"}
BLEND_NAMES = {"NORMAL": "srcOver", "DARKEN": "darken", "MULTIPLY": "multiply", "COLOR_BURN": "colorBurn",
               "CLASSIC_COLOR_BURN": "colorBurn", "ADD": "additive", "LINEAR_DODGE": "additive", "LIGHTEN": "lighten",
               "SCREEN": "screen", "COLOR_DODGE": "colorDodge", "CLASSIC_COLOR_DODGE": "colorDodge",
               "OVERLAY": "overlay", "SOFT_LIGHT": "softLight", "HARD_LIGHT": "hardLight", "DIFFERENCE": "difference",
               "CLASSIC_DIFFERENCE": "difference", "EXCLUSION": "exclusion", "HUE": "hue", "SATURATION": "saturation",
               "COLOR": "color", "LUMINOSITY": "luminosity", "LIGHTER_COLOR": "lighten", "DARKER_COLOR": "darken"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
RASTER_EXT = (".psd", ".tif", ".tiff", ".gif", ".bmp", ".tga", ".exr", ".heic", ".dpx")
VECTOR_EXT = (".ai", ".eps", ".pdf", ".svg")
VIDEO_EXT = (".mov", ".mp4", ".m4v", ".avi", ".mxf", ".webm", ".mkv", ".mpg", ".mpeg", ".gif")
AUDIO_EXT = (".wav", ".mp3", ".aif", ".aiff", ".m4a", ".aac", ".ogg", ".flac")
BIG = 200000.0
LUAU_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "luau")
_FXLIB = None


def fxlib_effects():
    """match name -> manifest of the AE-compatible WGSL effects library (ae2rml/fxlib)"""
    global _FXLIB
    if _FXLIB is None:
        from . import fxlib
        _FXLIB = fxlib.by_match_name()
    return _FXLIB


# AE modes Rive lacks -> the Rive mode of the same family (contrast / darken / lighten): drawn "normal", an opaque
# vivid-light tint hid rig-test's whole rewind transition under a flat teal
BLEND_NEAREST = {"VIVID_LIGHT": "hardLight", "LINEAR_LIGHT": "hardLight", "PIN_LIGHT": "hardLight",
                 "HARD_MIX": "hardLight", "LINEAR_BURN": "multiply", "SUBTRACT": "difference", "DIVIDE": "colorDodge"}


def blend_of(L):
    """(Rive blend mode, AE name when it is only the nearest one or not converted)"""
    try:
        bm = L.blending_mode
        name = getattr(bm, "name", None) or str(bm)
    except Exception:
        return "srcOver", None
    r = BLEND_NAMES.get(name)
    if r:
        return r, None
    return BLEND_NEAREST.get(name, "srcOver"), name


BAKE_JSX_BODY = r"""
(function () {
  function esc(s) { return String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\r/g, "\\r").replace(/\n/g, "\\n"); }
  function ser(v) {
    if (v === null || v === undefined) return "null";
    if (typeof v === "number") return isFinite(v) ? String(v) : "null";
    if (typeof v === "boolean") return v ? "1" : "0";
    if (typeof v === "string") return '"' + esc(v) + '"';
    if (v instanceof Array) { var a = []; for (var i = 0; i < v.length; i++) a.push(ser(v[i])); return "[" + a.join(",") + "]"; }
    if (v.vertices !== undefined) return '{"v":' + ser(v.vertices) + ',"i":' + ser(v.inTangents) + ',"o":' + ser(v.outTangents) + ',"c":' + (v.closed ? 1 : 0) + '}';
    if (v.text !== undefined) return '"' + esc(v.text) + '"';
    return "null";
  }
  function comp(id, name) {
    var byName = null;
    for (var i = 1; i <= app.project.numItems; i++) {
      var it = app.project.item(i);
      if (!(it instanceof CompItem)) continue;
      if (it.id == id) return it;
      if (it.name == name && byName === null) byName = it;
    }
    return byName;
  }
  function child(g, mn, k) {
    var n = 0;
    for (var i = 1; i <= g.numProperties; i++) { var q = g.property(i); if (q.matchName == mn) { if (n == k) return q; n++; } }
    return null;
  }
  var parts = [], missing = 0;
  for (var r = 0; r < R.length; r++) {
    var q = R[r], c = comp(q.compId, q.comp);
    if (!c || q.layer > c.numLayers) { missing++; continue; }
    var p = c.layer(q.layer);
    for (var s = 0; s < q.steps.length && p; s++) p = child(p, q.steps[s][0], q.steps[s][1]);
    if (!p || !p.valueAtTime) { missing++; continue; }
    var fps = c.frameRate, n = q.once ? 0 : Math.round(c.duration * fps), vals = [];
    for (var f = 0; f <= n; f++) { try { vals.push(ser(p.valueAtTime(f / fps, false))); } catch (e) { vals.push("null"); } }
    parts.push('"' + esc(q.key) + '":{"fps":' + fps + ',"values":[' + vals.join(",") + "]}");
  }
  var f = new File(OUT); f.encoding = "UTF-8"; f.open("w");
  f.write('{"entries":{' + parts.join(",") + '},"missing":' + missing + "}"); f.close();
})();
"""


def _patch_py_aep():
    """py-aep 0.17 scales every mask path by its layer's size, reading the stored bounding box as normalized [0, 1].
    That holds for footage / solid / precomp layers only: on a shape or text layer AE stores the mask in pixels
    (measured: FX Monster's Shape_01 masks came out at x = -1 177 920 = -613.5 × 1920, promo-test's text mask
    bbox 1117). Such masks are read unscaled."""
    from py_aep.models.properties import shape as _shape
    if getattr(_shape.Shape, "_ae2rml_patched", False):
        return
    size = _shape.Shape._comp_size.fget

    def comp_size(self):
        if type(getattr(self, "_layer", None)).__name__ in ("ShapeLayer", "TextLayer"):
            return None
        return size(self)
    _shape.Shape._comp_size = property(comp_size)
    _shape.Shape._ae2rml_patched = True


class Converter:
    def __init__(self, aep, out_dir, comp=None, fps=None, loop="loop", bg=True, log=print, media_scale=1.0,
                 media_fps=None, ae_lang=None, audio=True, video=True):
        import py_aep
        from .rml import PRUNED
        _patch_py_aep()
        PRUNED["keys"] = 0
        self.aep = os.path.abspath(aep)
        self.out_dir = os.path.abspath(out_dir)
        self.log = log
        try:
            self.app = py_aep.parse(self.aep)
        except Exception as ex:
            raise SystemExit(f"py-aep cannot read {os.path.basename(self.aep)} ({type(ex).__name__}: {str(ex)[:120]}).\n"
                             "Measured on 6 projects saved in 2020-2021: open it in a recent After Effects, save it "
                             "(File > Save As), then import that copy.")
        self.project = self.app.project
        base = os.path.splitext(os.path.basename(self.aep))[0]
        self.name = slug(base)
        self.build_dir = os.path.join(self.out_dir, "build", "ae2rml")
        os.makedirs(self.build_dir, exist_ok=True)
        os.makedirs(os.path.join(self.out_dir, "assets"), exist_ok=True)
        self.report = Report(os.path.basename(self.aep))
        self.ids = IdPool(os.path.join(self.build_dir, "idmap.json"))
        self.engine = Engine(self.project, self.report, lang=ae_lang, pseudo=pseudo_param_names(self.aep),
                             defaults=param_defaults(self.aep))
        self.load_bake()
        self.failed_expr = []
        self.fps_override = fps
        self.loop = loop
        self.bg = bg
        self.want_comp = comp
        self.builds = {}              # comp id (or (comp id, overrides signature)) -> CompBuild
        self.variants = {}            # comp id -> number of Essential Properties variants built
        self._reach = {}
        self.order = []
        self.assets = []              # root asset elements
        self.images = {}
        self.fonts = {}
        self.scripts = {}             # luau library name -> ScriptAsset id (modules first in the document)
        self.media_scale = media_scale
        self.with_audio = audio         # False: no sound at all (--no-audio)
        self.with_video = video         # False: video files left out (--no-video)
        self.media_fps = media_fps
        self.frames = {}              # (footage id, tag) -> {source frame index: (asset id, w, h)}
        self.media_bytes = 0
        self.main = None

    # ------------------------------------------------------------------ main comp
    def comps(self):
        return list(self.project.compositions)

    def roots(self):
        used = set()
        for c in self.comps():
            for L in c.layers:
                src = getattr(L, "source", None)
                if src is not None and hasattr(src, "layers"):
                    used.add(src.id)
        return [c for c in self.comps() if c.id not in used]

    def pick_main(self):
        comps = self.comps()
        if not comps:
            raise SystemExit("no composition in the project")
        if self.want_comp:
            for c in comps:
                if clean(c.name) == self.want_comp:
                    return c
            raise SystemExit(f"no composition named {self.want_comp!r}; comps: {', '.join(clean(c.name) for c in comps)}")
        roots = self.roots() or comps

        def weight(c, seen=None):
            seen = seen or set()
            if c.id in seen:
                return 0
            seen.add(c.id)
            w = len(c.layers)
            for L in c.layers:
                src = getattr(L, "source", None)
                if src is not None and hasattr(src, "layers"):
                    w += weight(src, seen)
            return w
        roots.sort(key=lambda c: (-weight(c), clean(c.name)))
        if len(roots) > 1:
            self.report.add("project", "info", "main comp",
                            f"main = '{clean(roots[0].name)}' (largest tree); other top-level comps: "
                            f"{', '.join(clean(c.name) for c in roots[1:6])}{' …' if len(roots) > 6 else ''} — use --comp")
        return roots[0]

    # ------------------------------------------------------------------ run
    def convert(self):
        main = self.pick_main()
        self.main = self.comp_build(main, main=True)
        self.unnest_fx()
        for cb in self.order:
            cb.finish()
        roots = []
        x = 0.0
        for cb in self.order:
            if cb is not self.main:
                cb.ab.set("x", x + float(self.main.comp.width) + 200.0).set("y", 0.0)
                x += float(cb.comp.width) + 200.0
            roots.append(cb.ab)
        for cb in self.order:
            if cb is not self.main:
                roots.append(E("ComponentAsset", artboardId=cb.ab.id, name=cb.ab.name))
        roots += self.script_assets() + self.assets
        scene = os.path.join(self.out_dir, "scene.rml")
        open(scene, "w", encoding="utf-8").write(document(roots))
        yml = os.path.join(self.out_dir, "rive.yaml")
        open(yml, "w", encoding="utf-8").write(f"name: {self.name}\nmain: {self.main.ab.name}\nlogs:\n  file: build/rive.log\n"
                             f"  problems: build/problems.log\n")
        if self.media_bytes > 8e6:
            self.report.add("project", "info", "image sequences",
                            f"{self.media_bytes / 1e6:.1f} MB of video / sequence frames embedded: --media-scale 0.5 "
                            f"divides that by ~4, --media-fps 12 halves the frame count")
        for tmp, _done in getattr(self, "_raw", {}).values():
            shutil.rmtree(tmp, ignore_errors=True)
        self.ids.save()
        self.write_side_files()
        return scene

    def unnest_fx(self):
        """An effect node rendered INSIDE another script's canvas (an effect node's source, a 3D plane…) draws nothing in
        Rive: a 2-D canvas that draws a GPU canvas's image gets it before the GPU passes run (measured: Tint inside
        Invert came out black, primed or not). Such nested effect nodes are neutralised — fxMix 0: they draw their
        content as is — so the outer effect works and nothing disappears; the report lists what was given up."""
        fx_sids = {sid for name, (sid, _m) in self.scripts.items() if name.startswith("fx_")}
        boards = {b.ab.id: b for b in self.order}
        # artboards drawn into a canvas by a script, then everything they contain (nested artboards, script inputs)
        todo = []
        for b in self.order:
            for e in b.ab.iter():
                if e.tag == "ScriptedDrawable":
                    todo += [c.attrs.get("artboardId") for c in e.children if c.tag == "ScriptInputArtboard"]
        seen = set()
        while todo:
            aid = todo.pop()
            if aid in seen or aid not in boards:
                continue
            seen.add(aid)
            for e in boards[aid].ab.iter():
                if e.tag == "NestedArtboard":
                    todo.append(e.attrs.get("artboardId"))
                elif e.tag == "ScriptInputArtboard":
                    todo.append(e.attrs.get("artboardId"))
        pk = prop_key("ScriptInputNumber", "propertyValue")
        n = 0
        for aid in seen:
            b = boards[aid]
            for e in b.ab.iter():
                if e.tag != "ScriptedDrawable" or e.attrs.get("scriptAssetId") not in fx_sids:
                    continue
                mix = next((c for c in e.children if c.tag == "ScriptInputNumber" and c.name == "fxMix"), None)
                if mix is None or float(mix.attrs.get("propertyValue", 1.0) or 0.0) == 0.0 and not any(
                        (mix.id, pk) in o.anim.tracks for o in self.order):
                    continue
                mix.set("propertyValue", 0.0)
                for o in self.order:
                    if (mix.id, pk) in o.anim.tracks:
                        del o.anim.tracks[(mix.id, pk)]
                        o.anim.order = [k for k in o.anim.order if k != (mix.id, pk)]
                n += 1
                self.report.add(getattr(b, "scope", b.ab.name), "approx", e.name,
                                "raster effects inside another raster effect's content are not rendered by Rive (a "
                                "canvas cannot show a GPU canvas drawn into it): this one is switched off (fxMix 0), "
                                "the outer one works")
        return n

    def write_side_files(self):
        todo = os.path.join(self.build_dir, "effects_todo.json")
        json.dump(self.report.todo_effects, open(todo, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        failed = os.path.join(self.build_dir, "expressions_failed.json")
        if self.failed_expr:
            json.dump([{"comp": c, "property": w, "expression": e, "error": m} for c, w, e, m in self.failed_expr],
                      open(failed, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        elif os.path.exists(failed):
            os.remove(failed)
        self.write_tag_jsx()
        self.write_bake_jsx()
        from .rml import PRUNED
        extra = (f"Source: `{self.aep}`  \nMain artboard: **{self.main.ab.name}** · artboards: {len(self.order)} · "
                 f"expressions evaluated offline: {self.engine_stats()} · redundant keys removed: {PRUNED['keys']}")
        if self.engine.bake:
            extra += f" · {len(self.engine.bake)} baked by After Effects (build/ae2rml/bake.json)"
        open(os.path.join(self.build_dir, "report.md"), "w", encoding="utf-8").write(self.report.markdown(extra))

    # ------------------------------------------------------------------ expressions After Effects must evaluate
    def load_bake(self):
        """values sampled by After Effects for expressions this evaluator cannot run (compiled JSXBIN, APIs it lacks):
        build/ae2rml/bake.json, written by bake_expressions.jsx"""
        path = os.path.join(self.build_dir, "bake.json")
        if not os.path.exists(path):
            return
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            return
        for key, e in (data.get("entries") or {}).items():
            vals = []
            for v in e.get("values", []):
                if isinstance(v, dict) and "v" in v:
                    v = ShapeValue(v["v"], v["i"], v["o"], bool(v.get("c")))
                elif isinstance(v, list):
                    v = [float(x) if isinstance(x, (int, float)) else x for x in v]
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    v = float(v)
                vals.append(v)
            if vals:
                self.engine.bake[key] = (float(e.get("fps", 30.0)), vals)

    def write_bake_jsx(self):
        """A read-only script for After Effects: samples, frame by frame, every property whose expression failed
        here and writes build/ae2rml/bake.json next to it; converting again then uses AE's own values."""
        from .aexpr import bake_key, bake_steps
        reqs = []
        for key, (comp, L, p) in self.engine.bake_requests.items():
            if key in self.engine.bake:
                continue
            reqs.append({"key": key, "compId": comp.id, "comp": clean(comp.name), "layer": L.index + 1,
                         "steps": [[mn, k] for mn, k in bake_steps(p)], "once": key in self.engine.bake_once})
        path = os.path.join(self.build_dir, "bake_expressions.jsx")
        if not reqs:
            if os.path.exists(path):
                os.remove(path)
            return
        out = os.path.join(self.build_dir, "bake.json")
        src = ("// ae2rml: sample in After Effects the expressions ae2rml could not evaluate offline (compiled JSXBIN,\n"
               "// missing APIs, random() / wiggle() whose generator is AE's own). Open the project, run this file\n"
               "// (File > Scripts > Run Script File), convert again.\n"
               "// Read-only: it changes nothing in the project.\n"
               f"var R = {json.dumps(reqs, ensure_ascii=False)};\n"
               f"var OUT = {json.dumps(out)};\n"
               + BAKE_JSX_BODY)
        open(path, "w", encoding="utf-8").write(src)
        self.report.add("project", "info", "expressions",
                        f"{len(reqs)} expression(s) need After Effects: run build/ae2rml/bake_expressions.jsx in AE "
                        f"with the project open, then convert again (their values are then AE's, frame by frame)")
        self.log(f"  {len(reqs)} expression(s) need After Effects: run {path} in AE, then convert again")

    def engine_stats(self):
        ok = len(self.engine.static) + len({k[0] for k in self.engine.cache})
        return f"{ok} ok, {len(self.engine.failed)} kept out (see expressions_failed.json)"

    def write_tag_jsx(self):
        """A script for the ORIGINAL AE project: layer comments `rive:<id>` + comp comments `rive:<artboard>|<anim>`,
        so `ae pull <rive project>` reads your AE edits back into scene.rml."""
        items = []
        for cb in self.order:
            if isinstance(cb, FxArtboard) or cb.variant:
                continue                  # an overrides variant: its AE comp is tagged by the plain build
            layers = [[L.index + 1, clean(L.name), f"rive:{nid}"] for L, nid in cb.tagged]
            items.append({"compId": cb.comp.id, "comp": clean(cb.comp.name), "tag": f"rive:{cb.ab.id}|{cb.anim.id}",
                          "layers": layers})
        js = json.dumps(items, ensure_ascii=False)
        src = ("// ae2rml: tag the original After Effects project for `ae pull` (run it in AE: File > Scripts > Run Script File)\n"
               "// Only empty comments or existing rive: tags are written; nothing else in the project changes.\n"
               f"var T = {js};\n"
               "app.beginUndoGroup('rive tags'); var n = 0, skipped = 0;\n"
               "function byId(id, name) { for (var i = 1; i <= app.project.numItems; i++) { var it = app.project.item(i);"
               " if (it instanceof CompItem && (it.id == id)) return it; }\n"
               "  for (var j = 1; j <= app.project.numItems; j++) { var it2 = app.project.item(j); if (it2 instanceof CompItem && it2.name == name) return it2; } return null; }\n"
               "function ok(c) { return c == '' || c.indexOf('rive:') == 0; }\n"
               "for (var k = 0; k < T.length; k++) { var c = byId(T[k].compId, T[k].comp); if (!c) continue;"
               " if (ok(c.comment)) c.comment = T[k].tag; else skipped++;\n"
               "  for (var l = 0; l < T[k].layers.length; l++) { var e = T[k].layers[l]; if (e[0] > c.numLayers) continue;"
               " var L = c.layer(e[0]); if (L.name != e[1]) continue; if (ok(L.comment)) { L.comment = e[2]; n++; } else skipped++; } }\n"
               "app.endUndoGroup();\n"
               # no alert(): a modal dialog freezes AE when the script is run remotely (AppleScript DoScriptFile)
               f"var LOG = new File({json.dumps(os.path.join(self.build_dir, 'tag.log'))}); LOG.open('w');"
               " LOG.write('rive tags: ' + n + ' layers tagged, ' + skipped + ' kept (they had a comment)\\nDONE'); LOG.close();\n")
        open(os.path.join(self.build_dir, "tag_ae_project.jsx"), "w", encoding="utf-8").write(src)

    # ------------------------------------------------------------------ comps
    def comp_build(self, comp, main=False, overrides=None, colors=None):
        """the comp's artboard. `overrides` = Essential Properties of a precomp instance ({id(source property):
        (fn, keyed, owner comp id, signature)}): AE renders that instance with those values, so the comp is built
        again as a variant artboard, once per distinct set"""
        ov = overrides or {}
        sig = tuple(sorted((o[3] for o in ov.values()), key=repr))
        colors = list(colors or [])
        if colors:
            sig = sig + (("colors",) + tuple(id(fx) for fx, _ in colors),)
        key = (comp.id, sig) if sig else comp.id
        cb = self.builds.get(key)
        if cb is not None:
            return cb
        variant = 0
        if sig:
            variant = self.variants[comp.id] = self.variants.get(comp.id, 0) + 1
        cb = CompBuild(self, comp, main, variant, colors)
        self.builds[key] = cb
        self.order.append(cb)
        prev = dict(aexpr.OVERRIDES)
        aexpr.OVERRIDES.clear()
        aexpr.OVERRIDES.update(ov)
        prev_ctx = self.engine.use_context(key if sig else None)
        try:
            cb.build()
        finally:
            aexpr.OVERRIDES.clear()
            aexpr.OVERRIDES.update(prev)
            self.engine.use_context(prev_ctx)
        return cb

    def comp_refs(self, comp):
        """names of the comps that expressions in `comp` or below read with comp("…") (cached)"""
        cache = self.__dict__.setdefault("_refs", {})
        r = cache.get(comp.id)
        if r is not None:
            return r
        r = cache[comp.id] = set()
        from .aexpr import _children
        pat = re.compile(r"""comp\(\s*["']([^"']+)["']\s*\)""")

        def walk(g, depth=0):
            if depth > 12:
                return
            for q in _children(g):
                try:
                    if getattr(q, "expression_enabled", False) and q.expression and "comp(" in q.expression:
                        r.update(pat.findall(q.expression))
                except Exception:
                    pass
                if hasattr(q, "properties"):
                    walk(q, depth + 1)
        for L in comp.layers:
            try:
                walk(L)
            except Exception:
                pass
            src = getattr(L, "source", None)
            if src is not None and hasattr(src, "layers"):
                r |= self.comp_refs(src)
        return r

    def reachable(self, comp):
        """ids of `comp` and of every comp nested in it (precomp layers, recursively)"""
        r = self._reach.get(comp.id)
        if r is not None:
            return r
        r = self._reach[comp.id] = {comp.id}
        for L in comp.layers:
            src = getattr(L, "source", None)
            if src is not None and hasattr(src, "layers"):
                r |= self.reachable(src)
        return r

    # ------------------------------------------------------------------ assets
    def script(self, name):
        """ScriptAsset id of a Luau file of ae2rml/luau (copied into the project); its modules come along"""
        if name not in self.scripts:
            src = os.path.join(LUAU_DIR, name + ".luau")
            text = open(src, encoding="utf-8").read()
            for mod in sorted(set(re.findall(r"require\('([a-z_0-9]+)'\)", text))):
                self.script(mod)
            shutil.copy(src, os.path.join(self.out_dir, name + ".luau"))
            # a protocol script returns its factory (`return function(): …`); anything else is a module
            self.scripts[name] = (self.ids.key("script", name), "return function(" not in text)
        return self.scripts[name][0]

    def fx_script(self, slugs, groups=None):
        """ScriptAsset id of the generated node running an fxlib effect stack (AE effects as WGSL, in stack order, in
        groups = one per AE layer); the shaders come along"""
        from . import fxlib
        name = fxlib.stack_name(slugs, groups)
        if name not in self.scripts:
            open(os.path.join(self.out_dir, name + ".luau"), "w", encoding="utf-8").write(fxlib.luau_for(slugs, groups))
            for slug in slugs:
                m = fxlib.manifest(slug)
                shutil.copy(fxlib.wgsl_path(m), os.path.join(self.out_dir, m["wgsl"]))
            shutil.copy(fxlib.MIX_WGSL, os.path.join(self.out_dir, "_ae_fx_mix.wgsl"))
            self.scripts[name] = (self.ids.key("script", name), False)
        return self.scripts[name][0]

    def script_assets(self):
        out = []
        for name, (sid, is_module) in sorted(self.scripts.items(), key=lambda kv: not kv[1][1]):
            el = E("ScriptAsset", id=sid, name=name, file=name + ".luau")
            if is_module:
                el.set("isModule", True)
            out.append(el)
        return out

    def font_asset(self, ps):
        if ps in self.fonts:
            return self.fonts[ps]
        rel = copy_font(ps, self.out_dir, self.report)
        path = os.path.join(self.out_dir, rel)
        fam, sty = font_names(path)
        aid = self.ids.key("font", ps)
        self.assets.append(E("FontAsset", id=aid, name=ps, file=rel))
        info = (aid, rel, font_metrics(path), fam, sty)
        self.fonts[ps] = info
        if self.report.fonts.get(ps) is None:
            self.report.add("assets", "approx", ps, "font not found on this Mac: Helvetica stands in, as in AE "
                                                    "(a system font: install the real one before shipping)")
        return info

    def image_asset(self, item, L, transform=None, tag=""):
        """footage item -> (asset id, width, height) or None"""
        key = (item.id, tag)
        if key in self.images:
            return self.images[key]
        ms = item.main_source
        raw = getattr(ms, "file", "") or ""
        path = find_footage(raw, self.aep)
        name = clean(item.name)
        self.report.footage[name] = path
        res = None
        if path:
            ext = os.path.splitext(path)[1].lower()
            files = getattr(ms, "file_names", None) or []
            seq = len(files) > 1 and not getattr(ms, "is_still", True)
            base = slug(os.path.splitext(name)[0]) + (f"_{tag}" if tag else "")
            out = os.path.join(self.out_dir, "assets", base + ".png")
            ok = False
            try:
                if ext in AUDIO_EXT:
                    self.report.add("assets", "unsupported", name, "audio footage: not converted (Rive AudioAsset to add by hand)")
                    self.images[key] = None
                    return None
                if ext in VIDEO_EXT and ext != ".gif":
                    ok = self._ffmpeg_frame(path, out)
                    self.report.add("assets", "approx", name, "video → its first frame as a still image")
                elif ext in VECTOR_EXT:
                    ok = self._sips(path, out)
                    self.report.add("assets", "approx", name, f"{ext} vector footage rasterized to PNG")
                elif ext in IMAGE_EXT or ext in RASTER_EXT:
                    from PIL import Image
                    fa = getattr(ms, "file_attributes", None) or {}
                    im = None
                    if ext in (".psd", ".psb") and "psd_layer_index" in fa:
                        # one LAYER of the PSD (AE imports a layered PSD as one footage item per layer): PIL only
                        # reads the flattened image — broadcast-test's pictos all came out as the whole poster
                        from .util import psd_layer_image
                        bounds = tuple(fa.get(k) for k in ("psd_layer_top", "psd_layer_left", "psd_layer_bottom",
                                                           "psd_layer_right"))
                        im = psd_layer_image(path, getattr(ms, "layer_name", "") or fa.get("psd_group_name", ""),
                                             bounds if None not in bounds else None, fa.get("psd_layer_index"))
                        if im is None:
                            self.report.add("assets", "approx", name, "PSD layer not found in the file: the whole "
                                                                      "(flattened) image is used")
                        else:
                            cw, ch = fa.get("psd_canvas_width"), fa.get("psd_canvas_height")
                            iw, ih = int(getattr(item, "width", 0) or 0), int(getattr(item, "height", 0) or 0)
                            if cw and ch and (iw, ih) == (cw, ch) and im.size != (cw, ch):
                                canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))   # imported at document size
                                canvas.paste(im, (fa["psd_layer_left"], fa["psd_layer_top"]))
                                im = canvas
                            if getattr(ms, "layer_styles", "") == "merge":
                                self.report.add("assets", "info", name, "PSD layer read without its layer styles")
                    if im is None:
                        im = Image.open(path)
                        im.load()
                    im = im.convert("RGBA")
                    im.save(out)
                    ok = True
                    if seq:
                        self.report.add("assets", "approx", name, "image sequence → its first image")
                else:
                    self.report.add("assets", "unsupported", name, f"footage type {ext} not handled")
            except Exception as ex:
                self.report.add("assets", "unsupported", name, f"footage could not be read ({ex})")
                ok = False
            if ok and os.path.exists(out):
                if transform is not None:
                    bake_image(out, transform)
                out = self._lighten(out)
                from PIL import Image
                w, h = Image.open(out).size
                aid = self.ids.key("image", item.id, tag)
                self.assets.append(E("ImageAsset", id=aid, name=base, file=os.path.relpath(out, self.out_dir)))
                res = (aid, w, h)
        else:
            self.report.add("assets", "unsupported", name, f"footage not found ({raw}): grey placeholder")
        self.images[key] = res
        return res

    def _lighten(self, out):
        """a still image as written: scaled by --media-scale, and JPEG when it is fully opaque (a photo stored as RGBA
        PNG weighed 10 MB: broadcast-test's packshots). The Image node rescales it to the footage size."""
        from PIL import Image
        try:
            im = Image.open(out)
            im.load()
            changed = False
            if self.media_scale < 0.999:
                im = im.resize((max(1, round(im.width * self.media_scale)), max(1, round(im.height * self.media_scale))),
                               Image.LANCZOS)
                changed = True
            opaque = im.mode != "RGBA" or im.getchannel("A").getextrema()[0] >= 255
            if opaque:
                jpg = os.path.splitext(out)[0] + ".jpg"
                im.convert("RGB").save(jpg, quality=90)
                os.remove(out)
                return jpg
            if changed:
                im.save(out)
        except Exception:
            pass
        return out

    def asset_size(self, aid):
        """(width, height) in pixels of an ImageAsset written by this conversion (still or sequence frame)"""
        for r in self.images.values():
            if r and r[0] == aid:
                return r[1], r[2]
        for d in self.frames.values():
            for r in (d or {}).values():
                if r and r[0] == aid:
                    return r[1], r[2]
        return None

    def audio_asset(self, item):
        key = ("audio", item.id)
        if key in self.images:
            return self.images[key]
        ms = item.main_source
        raw = getattr(ms, "file", "") or ""
        path = find_footage(raw, self.aep)
        name = clean(item.name)
        self.report.footage[name] = path
        aid = None
        if not path:
            self.report.add("assets", "unsupported", name, f"audio not found ({raw})")
        else:
            ext = os.path.splitext(path)[1].lower()
            base = slug(os.path.splitext(name)[0])
            if ext in (".wav", ".mp3", ".flac"):
                rel = f"assets/{base}{ext}"
                shutil.copy(path, os.path.join(self.out_dir, rel))
            else:
                rel = f"assets/{base}.wav"
                ok = shutil.which("ffmpeg") and subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", path, os.path.join(self.out_dir, rel)],
                    capture_output=True).returncode == 0
                if not ok:
                    self.report.add("assets", "unsupported", name, f"{ext} audio could not be converted to WAV (ffmpeg)")
                    self.images[key] = None
                    return None
                self.report.add("assets", "converted", name, f"{ext} audio → WAV (Rive decodes WAV/MP3/FLAC only)")
            aid = self.ids.key("audio", item.id)
            self.assets.append(E("AudioAsset", id=aid, name=base, file=rel))
        self.images[key] = aid
        return aid

    # ------------------------------------------------------------------ moving footage
    def media_info(self, item):
        """(path, source fps, source frame count, is video, has alpha, sequence files) of a video / image sequence"""
        ms = item.main_source
        path = find_footage(getattr(ms, "file", "") or "", self.aep)
        if not path:
            return None
        fps = float(getattr(ms, "conform_frame_rate", 0) or 0) or float(getattr(ms, "native_frame_rate", 0) or 0) \
            or float(getattr(item, "frame_rate", 0) or 0) or 25.0
        ext = os.path.splitext(path)[1].lower()
        video = ext in VIDEO_EXT and not getattr(ms, "target_is_folder", False)
        files = [] if video else sequence_files(path)
        loops = max(1, int(getattr(ms, "loop", 1) or 1))
        # py-aep leaves file_names empty offline: a sequence's frames are the numbered files beside its first one;
        # the item duration counts the loops
        n = len(files) if files else max(1, int(round(float(getattr(item, "duration", 0) or 0) * fps / loops)))
        return path, fps, n, video, bool(getattr(ms, "has_alpha", False)), files

    def media_frames(self, item, requests, tag=""):
        """frames of a video / image sequence as ImageAssets.
        requests: [(key, source frame index, colour transform or None)] -> {key: (asset id, w, h)}. The source frames
        are decoded once (lossless, in a temp folder); each key is written to assets/<footage>/f_<key> — JPEG when the
        footage has no alpha, PNG otherwise — with its own colour transform (an animated colour effect is baked per
        displayed frame)."""
        info = self.media_info(item)
        if info is None:
            return None
        path, _fps, _n, video, alpha, files = info
        cache = self.frames.setdefault((item.id, tag), {})
        todo = [(k, i, fn) for k, i, fn in requests if k not in cache]
        if not todo:
            return cache
        name = clean(item.name)
        raw = self.raw_frames(item, sorted({i for _, i, _ in todo}))
        if raw is None:
            return None
        from PIL import Image
        base = slug(os.path.splitext(name)[0]) + (f"_{tag}" if tag else "")
        folder = os.path.join(self.out_dir, "assets", base)
        os.makedirs(folder, exist_ok=True)
        ext = ".png" if alpha else ".jpg"
        for k, i, fn in todo:
            src = raw.get(i)
            if src is None:
                continue
            out = os.path.join(folder, f"f_{k}{ext}")
            im = Image.open(src)
            im.load()
            if alpha:
                im.convert("RGBA").save(out)
            else:
                im.convert("RGB").save(out, quality=88)
            if fn is not None:
                bake_image(out, fn)
            w, h = Image.open(out).size
            aid = self.ids.key("image", item.id, tag, "frame", k)
            self.assets.append(E("ImageAsset", id=aid, name=f"{base}_{k}", file=os.path.relpath(out, self.out_dir)))
            cache[k] = (aid, w, h)
            self.media_bytes += os.path.getsize(out)
        return cache

    def raw_frames(self, item, indices):
        """decode source frames once (scaled by --media-scale) -> {index: png path in a temp folder}"""
        import tempfile
        info = self.media_info(item)
        path, _fps, _n, video, _alpha, files = info
        name = clean(item.name)
        if not hasattr(self, "_raw"):
            self._raw = {}
        tmp, done = self._raw.setdefault(item.id, (tempfile.mkdtemp(prefix="ae2rml_"), {}))
        need = [i for i in indices if i not in done]
        sc = self.media_scale
        if need and video:
            if not shutil.which("ffmpeg"):
                self.report.add("assets", "unsupported", name, "video: ffmpeg not found, cannot extract its frames")
                return None
            a, b = need[0], need[-1]
            vf = f"select=between(n\\,{a}\\,{b})"
            if abs(sc - 1) > 1e-6:
                vf += f",scale=trunc(iw*{sc}/2)*2:trunc(ih*{sc}/2)*2"
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-vf", vf, "-fps_mode", "passthrough",
                                "-start_number", str(a), os.path.join(tmp, "r_%05d.png")], capture_output=True, text=True)
            if r.returncode != 0:
                self.report.add("assets", "unsupported", name, f"video frames could not be extracted ({r.stderr[-160:]})")
                return None
            for i in range(a, b + 1):
                f = os.path.join(tmp, f"r_{i:05d}.png")
                if os.path.exists(f):
                    done[i] = f
        elif need:
            from PIL import Image
            for i in need:
                src = files[min(i, len(files) - 1)] if files else path
                try:
                    im = Image.open(src)
                    im.load()
                    if abs(sc - 1) > 1e-6:
                        im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
                    f = os.path.join(tmp, f"r_{i:05d}.png")
                    im.save(f)
                    done[i] = f
                except Exception as ex:
                    self.report.add("assets", "unsupported", name, f"sequence frame {i} unreadable ({ex})")
        return {i: done[i] for i in indices if i in done}

    def media_audio(self, item, s0, s1):
        """the audio track of a video, cut to source seconds [s0, s1] -> AudioAsset id (None when it has none)"""
        info = self.media_info(item)
        if info is None or not info[3] or not shutil.which("ffmpeg"):
            return None
        path = info[0]
        key = ("vaudio", item.id, round(s0, 4), round(s1, 4))
        if key in self.images:
            return self.images[key]
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                "-of", "csv=p=0", path], capture_output=True, text=True)
        aid = None
        if probe.returncode == 0 and probe.stdout.strip():
            base = slug(os.path.splitext(clean(item.name))[0])
            rel = f"assets/{base}_audio_{int(s0 * 1000)}.wav"
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, s0):.4f}", "-t",
                                f"{max(0.01, s1 - s0):.4f}", "-i", path, "-vn", os.path.join(self.out_dir, rel)],
                               capture_output=True)
            if r.returncode == 0:
                aid = self.ids.key("audio", item.id, "video", round(s0, 4))
                self.assets.append(E("AudioAsset", id=aid, name=f"{base} audio", file=rel))
        self.images[key] = aid
        return aid

    def trimmed_audio(self, item, s0, s1):
        """an audio file cut to source seconds [s0, s1] (the part of it the AE layer plays) -> AudioAsset id"""
        ms = item.main_source
        path = find_footage(getattr(ms, "file", "") or "", self.aep)
        if not path or not shutil.which("ffmpeg"):
            return None
        key = ("taudio", item.id, round(s0, 4), round(s1, 4))
        if key in self.images:
            return self.images[key]
        base = slug(os.path.splitext(clean(item.name))[0])
        rel = f"assets/{base}_{int(s0 * 1000)}_{int(s1 * 1000)}.wav"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, s0):.4f}", "-t",
                            f"{max(0.01, s1 - s0):.4f}", "-i", path, os.path.join(self.out_dir, rel)], capture_output=True)
        aid = None
        if r.returncode == 0:
            aid = self.ids.key("audio", item.id, "cut", round(s0, 4), round(s1, 4))
            self.assets.append(E("AudioAsset", id=aid, name=f"{base} {s0:.2f}-{s1:.2f}", file=rel))
        self.images[key] = aid
        return aid

    @staticmethod
    def _ffmpeg_frame(src, out):
        if not shutil.which("ffmpeg"):
            return False
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-frames:v", "1", out], capture_output=True)
        return r.returncode == 0 and os.path.exists(out)

    @staticmethod
    def _sips(src, out):
        # sips reads an Illustrator file only under a .pdf name (an .ai saved with PDF compatibility IS a PDF:
        # broadcast-test's festival logo came out as a grey placeholder)
        tmp = None
        try:
            with open(src, "rb") as fh:
                pdf = fh.read(5) == b"%PDF-"
        except OSError:
            pdf = False
        if pdf and not src.lower().endswith(".pdf"):
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            shutil.copyfile(src, tmp)
        try:
            r = subprocess.run(["sips", "-s", "format", "png", tmp or src, "--out", out], capture_output=True)
            return r.returncode == 0 and os.path.exists(out)
        finally:
            if tmp:
                os.remove(tmp)


def _parent_of(root, node):
    for e in root.iter():
        if node in e.children:
            return e
    return None


def sequence_files(first):
    """numbered image sequence: every file beside `first` with the same prefix and extension, in frame order"""
    d, name = os.path.split(first)
    m = re.match(r"^(.*?)(\d+)(\.[A-Za-z0-9]+)$", name)
    if not m or not os.path.isdir(d):
        return [first]
    prefix, ext = m.group(1), m.group(3).lower()
    out = []
    for fn in os.listdir(d):
        mm = re.match(r"^(.*?)(\d+)(\.[A-Za-z0-9]+)$", fn)
        if mm and mm.group(1) == prefix and mm.group(3).lower() == ext and not fn.startswith("._"):
            out.append((int(mm.group(2)), os.path.join(d, fn)))
    return [p for _, p in sorted(out)] or [first]


def font_names(path):
    try:
        from fontTools.ttLib import TTFont
        n = TTFont(path, lazy=True)["name"]
        fam = n.getDebugName(16) or n.getDebugName(1)
        sty = n.getDebugName(17) or n.getDebugName(2)
        return fam, sty
    except Exception:
        return None, None


def bake_image(path, fn):
    """apply a colour transform (rgb 0..1 -> rgb) to every pixel of a PNG, alpha kept"""
    try:
        import numpy as np
        from PIL import Image
        im = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32) / 255.0
        rgb = im[..., :3].reshape(-1, 3)
        # vectorised through a 3D LUT (the colour functions are per-pixel and scalar-coded)
        n = 17
        grid = np.linspace(0, 1, n)
        lut = np.zeros((n, n, n, 3), np.float32)
        for i, r in enumerate(grid):
            for j, g in enumerate(grid):
                for k, b in enumerate(grid):
                    lut[i, j, k] = fn([r, g, b])
        idx = rgb * (n - 1)
        i0 = np.clip(np.floor(idx).astype(int), 0, n - 2)
        f = idx - i0
        out = np.zeros_like(rgb)
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    w = ((f[:, 0] if dx else 1 - f[:, 0]) * (f[:, 1] if dy else 1 - f[:, 1]) * (f[:, 2] if dz else 1 - f[:, 2]))
                    out += w[:, None] * lut[i0[:, 0] + dx, i0[:, 1] + dy, i0[:, 2] + dz]
        im[..., :3] = out.reshape(im[..., :3].shape)
        out = Image.fromarray((np.clip(im, 0, 1) * 255).astype(np.uint8), "RGBA")
        if path.lower().endswith((".jpg", ".jpeg")):
            out.convert("RGB").save(path, quality=88)
        else:
            out.save(path)
    except Exception:
        pass


# ====================================================================== one composition
def _owner_comp(p):
    """the composition holding a py-aep property (None if it does not resolve)"""
    oc = getattr(p, "_owning_comp", None)
    try:
        return oc() if callable(oc) else oc
    except Exception:
        return None


class FxArtboard:
    """a sub-artboard holding what an AE raster effect (fxlib WGSL node) applies to: a layer's content, or every layer
    below an adjustment layer. Its keys are taken from the owner comp's animation when the owner finishes (an object
    is keyed by the LinearAnimation of the artboard it lives in)"""

    def __init__(self, owner, key, name, w, h):
        import types
        conv = owner.conv
        self.conv, self.owner, self.main = conv, owner, False
        self.comp = types.SimpleNamespace(width=w, height=h)
        self.ab = E("Artboard", id=conv.ids.key("comp", owner.kid, "fx", *key), name=name, width=float(w), height=float(h))
        self.anim = Anim(conv.ids.key("comp", owner.kid, "fx", *key, "anim"), name, owner.anim.fps, owner.anim.duration,
                         conv.loop)
        conv.order.append(self)
        owner.fx_subs.append(self)

    def take_tracks(self):
        ids = {e.id for e in self.ab.iter() if e.id}
        src = self.owner.anim
        for k in [k for k in src.order if k[0] in ids]:
            self.anim.order.append(k)
            self.anim.tracks[k] = src.tracks.pop(k)
        src.order = [k for k in src.order if k[0] not in ids]

    def finish(self):
        self.ab.set("isComponent", True)
        self.ab.set("clip", True)
        self.ab.add(self.anim.element())


class CompBuild:
    def __init__(self, conv, comp, main, variant=0, colors=None):
        self.conv, self.comp, self.main = conv, comp, main
        self.inherited_colors = list(colors or [])   # [(fx, fn)] colour effects of the precomp layer(s) above
        self.variant = variant        # > 0: built with a precomp instance's Essential Properties overrides
        self.kid = comp.id if not variant else f"{comp.id}~{variant}"      # id namespace
        name = clean(comp.name) + (f" · instance {variant}" if variant else "")
        fps = conv.fps_override or max(1, int(round(float(comp.frame_rate or 25))))
        self.tl = Timeline(conv, comp, fps)
        ids = conv.ids
        self.ab = E("Artboard", id=ids.key("comp", self.kid), name=name, width=float(comp.width),
                    height=float(comp.height))
        self.anim = Anim(ids.key("comp", self.kid, "anim"), name, fps, self.tl.nframes, conv.loop)
        self.layers = list(comp.layers)
        self.children = {}
        for L in self.layers:
            if L.parent is not None:
                self.children.setdefault(L.parent.index, []).append(L)
        # a solo only hides the other layers' PICTURE when the soloed layer has one: a soloed sound or guide layer
        # leaves every image layer on (rig-test: "UniversalAudio-1" soloed in each character rig)
        self.solo = any(getattr(L, "solo", False) and self._has_picture(L) for L in self.layers)
        self.spine = [(None, self.ab)]
        self.canonical = {}           # layer index -> frame node id
        self.frames = {}              # layer index -> frame E (canonical)
        self.ghosts = {}
        self.tagged = []              # (layer, node id) for the AE tag script
        self.matte_src = {}           # layer index -> (normal id, inverted id)
        self.matte_users = {}
        self.clip_uses = [0, 0]       # (clipped, collapsed) uses as a precomp
        self.stencils = []            # (source node id, silhouette) of this comp's stencil / silhouette layers
        self.collapsed_stencil = None  # (NestedArtboard, its CompBuild): set while placing a collapsed precomp
        self.events = []
        self.group_effects = []       # GroupEffect (3D / Corner Pin projection) shared by a layer's paints
        self.depth_runs = []          # consecutive 3D layers whose depth order changes: (layers, order per frame)
        self.fx_subs = []             # FxArtboard of the raster effects (fxlib WGSL) placed in this comp
        self.adj_chains = []          # effect nodes of consecutive adjustment layers (one node, one group per layer)
        self.adj_open = None          # the chain the next adjustment layer joins (closed by any drawn layer)
        self.scope = name
        for L in self.layers:
            m = self.matte_of(L)
            if m is not None:
                self.matte_users.setdefault(m.index, []).append(L)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _has_picture(L):
        if getattr(L, "guide_layer", False):
            return False
        src = getattr(L, "source", None)
        if type(L).__name__ == "AVLayer" and src is not None and not hasattr(src, "layers"):
            if not getattr(src, "width", 0) or getattr(src, "has_video", True) is False:
                return False
        return True

    def matte_empty(self, L, depth=0):
        """L's (non-inverted) track matte lets nothing through: the matte is a null layer (no pixels), or a layer
        whose own matte lets nothing through — AE 2023+ renders a matte layer with its own matte (Glass Animation 01:
        'Stroke Sharp N2' matted by a null, N1 by N2, A by N1 — none of them shows)"""
        m = self.matte_of(L)
        if m is None or depth > 32 or int(getattr(L, "track_matte_type", 5012)) in (5014, 5016):
            return False
        return bool(getattr(m, "null_layer", False)) or self.matte_empty(m, depth + 1)

    def matte_of(self, L):
        try:
            t = int(L.track_matte_type)
        except Exception:
            return None
        if t in (5012, 0):
            return None
        try:
            m = L.track_matte_layer
        except Exception:
            m = None
        if m is None and L.index > 0:
            m = self.layers[L.index - 1]
        return m

    def is_matte(self, L):
        return L.index in self.matte_users

    def drawn(self, L):
        kind = type(L).__name__
        if kind in ("CameraLayer", "LightLayer"):
            return False
        if getattr(L, "guide_layer", False):
            return False
        if not getattr(L, "enabled", True):
            return False                          # a track matte layer is hidden by its own switch (AE turns it off)
        if self.solo and not getattr(L, "solo", False):
            return False
        if getattr(L, "adjustment_layer", False):
            return False
        if kind == "AVLayer" and getattr(L, "null_layer", False):
            return False
        src = getattr(L, "source", None)
        if kind == "AVLayer" and src is not None and not hasattr(src, "layers"):
            ms = getattr(src, "main_source", None)
            f = (getattr(ms, "file", "") or "").lower()
            if f.endswith(AUDIO_EXT) and not getattr(src, "width", 0):
                return False
        return True

    def chain(self, L):
        out, cur, seen = [], L.parent, set()
        while cur is not None and cur.index not in seen:
            out.append(cur)
            seen.add(cur.index)
            cur = cur.parent
        return list(reversed(out))

    # ---------------------------------------------------------------- build
    def draw_order(self):
        """AE draws consecutive 3D layers by distance to the camera, nearest on top (a 2D layer splits the run);
        Rive draws earlier siblings on top: the run is sorted nearest first, at mid-comp."""
        out, run = [], []
        pr = None

        def depth(L, t):
            # the centre of the layer's content (two pages hinged on one anchor would tie on their anchors)
            if type(L).__name__ == "ShapeLayer":
                from .shapes import layer_bounds
                x0, y0, x1, y1 = layer_bounds(L, self.conv.engine, self.comp, t)
            else:
                src = getattr(L, "source", None)
                x0, y0 = 0.0, 0.0
                x1 = float(getattr(src, "width", 0) or self.comp.width)
                y1 = float(getattr(src, "height", 0) or self.comp.height)
            return pr.depth(L, t, ((x0 + x1) / 2.0, (y0 + y1) / 2.0))

        def flush():
            if len(run) > 1:
                fps = self.tl.fps
                orders = [tuple(sorted(run, key=lambda L: depth(L, f / fps))) for f in range(self.tl.nframes + 1)]
                out.extend(orders[0])
                if any(o != orders[0] for o in orders):
                    # the order changes: DrawRules on each layer, keyed per frame to the slot of its rank (finish())
                    self.depth_runs.append((list(run), orders))
            else:
                out.extend(run)
            run.clear()
        for L in self.layers:
            if three.is3d(L) and self.drawn(L):
                if pr is None:
                    pr = self.conv.engine.projector(self.comp, None)
                run.append(L)
            else:
                flush()
                out.append(L)
        flush()
        return out

    def build(self):
        for L in self.draw_order():
            kind = type(L).__name__
            if kind == "CameraLayer":
                self.conv.report.add(self.scope, "converted", clean(L.name),
                                     "camera → projection of the 3D layers (and of toComp in expressions)")
                try:
                    dof = L.property("ADBE Camera Options Group").property("ADBE Camera Depth of Field")
                    if dof is not None and tonum(dof.value) > 0.5:
                        self.conv.report.add(self.scope, "approx", clean(L.name), "depth of field (blur) is not rendered")
                except Exception:
                    pass
            elif kind == "LightLayer":
                self.conv.report.add(self.scope, "unsupported", clean(L.name), "light: not converted (no shading in Rive)")
            elif getattr(L, "adjustment_layer", False) and getattr(L, "enabled", True):
                self.adjust(LayerBuild(self, L))
            if self.is_audio(L):
                LayerBuild(self, L).audio()
            if self.drawn(L) or self.is_matte(L):
                self.pending_stencil = None
                self.collapsed_stencil = None
                self.adj_open = None              # a drawn layer between two adjustment layers: separate nodes
                self.place(L)
                self.collapsed_adjustments(L)
                if not self.pending_stencil and self.collapsed_stencil:
                    self.pending_stencil = LayerBuild(self, L).stencil_from_collapsed(*self.collapsed_stencil)
                    self.collapsed_stencil = None
                if self.pending_stencil:
                    sid, inverted = self.pending_stencil
                    wrap = self.spine[0][1].add(E("Node", id=self.conv.ids.key("comp", self.kid, "stencil", L.id),
                                                  name=f"below {clean(L.name)}"))
                    wrap.add(E("ClippingShape", id=self.conv.ids.key("comp", self.kid, "stencil", L.id, "c"),
                               name="Stencil / silhouette", sourceId=sid, fillRule="evenOdd" if inverted else None))
                    self.spine = [(None, wrap)]
                    self.pending_stencil = None
        for chain in self.adj_chains:
            self.fill_fx_node(chain["sd"], chain["groups"], chain["sub"], chain["owner"], ("adj",))

    # ---------------------------------------------------------------- adjustment layers
    def adjust(self, lb, offset_ok=True):
        """one adjustment layer (of this comp, or of a collapsed precomp placed above): its Transform effect wraps the
        layers below in nodes; its fxlib effects join the open chain (consecutive adjustment layers = ONE effect node,
        a group per layer — nested effect nodes would not render) or start one whose sub-artboard receives the layers
        below"""
        L = lb.L
        try:
            fxs_all = [fx for fx in L.effects.properties if getattr(fx, "enabled", True)]
        except Exception:
            fxs_all = []
        geo = [fx for fx in fxs_all if getattr(fx, "match_name", "") == "ADBE Geometry2"]
        lib = fxlib_effects()
        wg = [fx for fx in fxs_all if getattr(fx, "match_name", "") in lib]
        if geo:
            # a Transform effect on an adjustment layer moves everything below it: the layers placed from here on
            # (Rive: later siblings are below) go inside its nodes (broadcast-test's "Move" zooms per shot)
            inner = lb.adjustment_transform(geo, self.spine[0][1])
            self.spine = [(None, inner)]
            self.adj_open = None
        if wg:
            group = lb.adjustment_group(wg)
            chain = self.adj_open
            if chain is not None:
                chain["groups"].insert(0, group)          # lower adjustment layer: applied first
            else:
                w, h = float(self.comp.width), float(self.comp.height)
                sub = FxArtboard(self, (L.id, "adj"), f"{self.scope} · below {lb.name}", w, h)
                sd = E("ScriptedDrawable", id=lb.id("wgsl", "adj"), name=f"{lb.name} · effects")
                self.spine[0][1].add(sd)
                chain = {"sd": sd, "sub": sub, "groups": [group], "owner": lb}
                self.adj_chains.append(chain)
                self.adj_open = chain
                self.spine = [(None, sub.ab)]
        lb.adjustment_note(skip=(("ADBE Geometry2",) if geo else ()) + tuple(fx.match_name for fx in wg))

    def collapsed_adjustments(self, L):
        """AE: the adjustment layers of a COLLAPSED precomp apply to the layers below that precomp in this comp (Minimal
        Promo's 'Color Correction' over the whole promo) — they act here as adjustment layers placed under it"""
        src = getattr(L, "source", None)
        if type(L).__name__ != "AVLayer" or not hasattr(src, "layers") or not getattr(L, "collapse_transformation", False):
            return
        adjs = [A for A in src.layers if getattr(A, "adjustment_layer", False) and getattr(A, "enabled", True)]
        if not adjs:
            return
        st = float(getattr(L, "start_time", 0.0) or 0.0)
        stretch = float(getattr(L, "stretch", 100.0) or 100.0)
        if abs(st) > 1e-6 or abs(stretch - 100.0) > 1e-6 or getattr(L, "time_remap_enabled", False) \
                or abs(float(src.frame_rate or 0) - float(self.comp.frame_rate or 0)) > 1e-3:
            self.conv.report.add(self.scope, "approx", clean(L.name), "adjustment layers of this collapsed precomp are not "
                                 "applied to the layers below it (shifted / stretched / remapped precomp time)")
            return
        for A in adjs:
            self.adjust(LayerBuild(self, A))
        self.conv.report.add(self.scope, "converted", clean(L.name), f"collapsed precomp: its {len(adjs)} adjustment "
                             f"layer{'s' if len(adjs) > 1 else ''} apply to the layers below it here (as AE does)")

    def fill_fx_node(self, sd, groups, sub, owner, key, pad=0.0):
        """the ScriptedDrawable `sd` runs the fxlib node of `groups` ([{lb, fxs, amount, blend}], first applied first:
        one per AE layer) over the artboard `sub` played at the comp's time; inputs = each effect's AE parameters in AE
        units (e1_, e2_… keyed like the AE properties) + each group's mix / blend"""
        from .shapes import _stored
        from .fxlib import param_match_name
        conv = self.conv
        lib = fxlib_effects()
        flat = [(g, fx, lib[fx.match_name]) for g in groups for fx in g["fxs"]]
        gidx, k = [], 1
        for g in groups:
            gidx.append(list(range(k, k + len(g["fxs"]))))
            k += len(g["fxs"])
        sd.set("scriptAssetId", conv.fx_script([m["slug"] for _g, _fx, m in flat], gidx))
        sd.add(E("ScriptInputArtboard", id=owner.id("wgsl", *key, "src"), name="fxSource", artboardId=sub.ab.id))
        sd.add(E("ScriptInputString", id=owner.id("wgsl", *key, "anim"), name="fxAnimation", propertyValue=sub.anim.name))
        n, fps = self.tl.nframes, float(self.tl.fps)
        tin = sd.add(E("ScriptInputNumber", id=owner.id("wgsl", *key, "time"), name="fxTime", propertyValue=0.0))
        owner.put_dense(tin, "propertyValue", [(0, 0.0), (n, n / fps)])
        sd.add(E("ScriptInputNumber", id=owner.id("wgsl", *key, "pad"), name="fxPad", propertyValue=float(pad)))
        single = len(groups) == 1
        for j, g in enumerate(groups, 1):
            mn_, bn_ = ("fxMix", "fxBlend") if single else (f"g{j}Mix", f"g{j}Blend")
            am = sd.add(E("ScriptInputNumber", id=g["lb"].id("wgsl", *key, "mix", j), name=mn_, propertyValue=1.0))
            if g.get("amount"):
                g["lb"].put_dense(am, "propertyValue", g["amount"])
            sd.add(E("ScriptInputNumber", id=g["lb"].id("wgsl", *key, "blend", j), name=bn_,
                     propertyValue=float(g.get("blend", 0))))
        animated_colors = []
        for i, (g, fx, m) in enumerate(flat, 1):
            lb = g["lb"]
            P = {getattr(q, "match_name", ""): q for q in lb.fx_params(fx)}
            for p in m["params"]:
                q = P.get(param_match_name(fx.match_name, p["ae"]))     # position -> matchName (they differ: ADBE Fill)
                ap = lb.prop(q, f"{clean(fx.name)} {p['field']}") if q is not None and _stored(q) else None
                d = p.get("default", 0)
                field = f"e{i}_{p['field']}"
                if p["kind"] == "color":
                    v = unwrap(ap.at(0.0)) if ap is not None and ap.animated else (ap.static(None, d) if ap is not None else d)
                    if ap is not None and ap.animated:
                        animated_colors.append(f"{clean(fx.name)} {p['field']}")
                    v = list(v) if isinstance(v, (list, tuple)) else list(d)
                    sd.add(E("ScriptInputColor", id=lb.id("wgsl", *key, "e", i, "p", p["field"]), name=field,
                             propertyValue=argb(v[:3], v[3] if len(v) > 3 else 1.0)))
                elif p["kind"] == "point":
                    for dim, axis in ((0, "X"), (1, "Y")):
                        el = sd.add(E("ScriptInputNumber", id=lb.id("wgsl", *key, "e", i, "p", p["field"], axis),
                                      name=field + axis, propertyValue=float(d[dim]) if isinstance(d, list) else 0.0))
                        if ap is not None:
                            lb.put(el, "propertyValue", ap, dim, force=True)
                else:
                    el = sd.add(E("ScriptInputNumber", id=lb.id("wgsl", *key, "e", i, "p", p["field"]), name=field,
                                  propertyValue=float(d[0] if isinstance(d, list) else d)))
                    if ap is not None:
                        lb.put(el, "propertyValue", ap, None, force=True)
            if "map" in m.get("textures", {}).values():
                self.bind_map_layer(sd, lb, fx, m, i, sub, key)
            if m.get("notes"):
                sd.comments.append(f"fxlib {m['slug']}: {m['notes']}")
        if animated_colors:
            self.conv.report.add(self.scope, "approx", owner.name, f"animated colour ({', '.join(animated_colors)}): its first value is used")
        return sd

    def bind_map_layer(self, sd, lb, fx, m, i, sub, key):
        """an effect that reads a second layer (the shader's `mapTex`; manifest `textureParams.mapTex` = the AE position of
        its layer parameter): bind the node's e<i>_mapSource to an artboard showing that layer's SOURCE (AE effects read a
        layer's source, before its masks, effects and transform). A precomp -> its comp artboard; a still image -> a
        sub-artboard with the image; the layer itself (AE's default) -> the effect's own source artboard."""
        from .fxlib import param_match_name
        conv = self.conv
        idx = (m.get("textureParams") or {}).get("mapTex")
        if idx is None:
            return
        try:
            num = int(param_match_name(fx.match_name, idx).split("-")[-1])
        except ValueError:
            num = int(idx)
        P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in lb.fx_params(fx)
             if getattr(p, "match_name", "").startswith(fx.match_name + "-")}
        q = P.get(num)
        try:
            ref = int(round(tonum(q.value))) if q is not None else 0
        except Exception:
            ref = 0
        layers = self.layers
        target = layers[ref - 1] if 1 <= ref <= len(layers) else None
        what, aid = None, None
        if target is None or target is lb.L:
            aid, what = sub.ab.id, "the layer itself"
        else:
            src = getattr(target, "source", None)
            if src is not None and hasattr(src, "layers"):
                aid, what = conv.comp_build(src).ab.id, f"precomp '{clean(target.name)}'"
            elif src is not None and type(getattr(src, "main_source", None)).__name__ == "FileSource" \
                    and getattr(src.main_source, "is_still", True):
                res = conv.image_asset(src, target)
                if res:
                    w, h = float(getattr(src, "width", 0) or res[1]), float(getattr(src, "height", 0) or res[2])
                    fa = FxArtboard(self, (lb.L.id, "map", i, *key), f"{self.scope} · {clean(target.name)} · map", w, h)
                    img = E("Image", id=conv.ids.key("comp", self.kid, "fxmap", lb.L.id, i, *key), name=clean(target.name),
                            assetId=res[0], originX=0.0, originY=0.0)
                    if res[1] and abs(res[1] - w) > 0.5:
                        img.set("scaleX", w / res[1]).set("scaleY", h / res[2] if res[2] else 1.0)
                    fa.ab.add(img)
                    aid, what = fa.ab.id, f"image '{clean(target.name)}'"
        if aid is None:
            conv.report.add(self.scope, "approx", clean(fx.name),
                            f"map layer '{clean(target.name)}' ({type(getattr(target, 'source', None)).__name__}): only "
                            "precomps, still images and the layer itself can be bound; the map is empty")
            return
        sd.add(E("ScriptInputArtboard", id=lb.id("wgsl", *key, "e", i, "map"), name=f"e{i}_mapSource", artboardId=aid))
        conv.report.add(self.scope, "converted", clean(fx.name), f"map layer: {what}")

    def is_audio(self, L):
        src = getattr(L, "source", None)
        if type(L).__name__ != "AVLayer" or src is None or hasattr(src, "layers"):
            return False
        f = (getattr(getattr(src, "main_source", None), "file", "") or "").lower()
        return f.endswith(AUDIO_EXT) and bool(getattr(L, "audio_enabled", True)) and not (self.solo and not getattr(L, "solo", False))

    def place(self, L):
        ext = self.chain(L) + [L]
        j = 0
        while j < len(ext) and j + 1 < len(self.spine) and self.spine[j + 1][0].index == ext[j].index:
            j += 1
        del self.spine[j + 1:]
        for lay in ext[j:]:
            space = self.spine[-1][1]
            frame, inner = self.frame_node(lay)
            space.add(frame)
            self.spine.append((lay, inner))
        LayerBuild(self, L).content(self.spine[-1][1])

    def frame_node(self, L):
        n = self.ghosts.get(L.index, -1) + 1
        self.ghosts[L.index] = n
        lb = LayerBuild(self, L)
        node, inner = lb.frame(n)
        if n == 0:
            self.canonical[L.index] = node.id
            self.frames[L.index] = node
            self.tagged.append((L, node.id))
        else:
            self.conv.report.add(self.scope, "info", clean(L.name), "parent repeated (ghost node) to keep AE's stacking order")
        return node, inner

    # ---------------------------------------------------------------- finish
    def depth_rules(self):
        """3D layers whose depth order changes: N empty slot shapes stacked where the run starts (the first one on
        top); each layer's node gets DrawRules with one DrawTarget per slot (`before` = drawn just above it) and
        drawTargetId keyed (hold) to the slot of its rank, nearest first — measured: a rule on a Node moves its
        whole subtree, an empty Shape is a valid target."""
        ids = self.conv.ids
        for k, (layers, orders) in enumerate(self.depth_runs):
            nodes = {L.index: self.frames.get(L.index) for L in layers}
            first = next((nodes[L.index] for L in orders[0] if nodes.get(L.index) is not None), None)
            if first is None:
                continue
            parent = _parent_of(self.ab, first)
            if parent is None:
                continue
            slots = [E("Shape", id=ids.key("comp", self.kid, "depth", k, "slot", r), name=f"3D depth slot {r + 1}")
                     for r in range(len(layers))]
            at = parent.children.index(first)
            parent.children[at:at] = slots
            for L in layers:
                node = nodes.get(L.index)
                if node is None:
                    continue
                rules = E("DrawRules", id=ids.key("comp", self.kid, "depth", k, "rules", L.index), name="3D depth")
                targets = []
                for r, slot in enumerate(slots):
                    t = rules.add(E("DrawTarget", id=ids.key("comp", self.kid, "depth", k, "t", L.index, r),
                                    name=f"rank {r + 1}", drawableId=slot.id, placementValue="before"))
                    targets.append(t.id)
                keys, last = [], None
                for f, order in enumerate(orders):
                    r = order.index(L)
                    if r != last:
                        keys.append((f, targets[r], "hold", None))
                        last = r
                rules.set("drawTargetId", keys[0][1])
                if len(keys) > 1:
                    self.anim.put(rules.id, "DrawRules", "drawTargetId", keys, kind="id")
                node.add(rules)
            changes = sum(1 for a, b in zip(orders, orders[1:]) if a != b)
            self.conv.report.add(self.scope, "converted", ", ".join(clean(L.name) for L in layers[:4]),
                                 f"3D depth order changes {changes}× → DrawRules keyed per frame (nearest on top)")

    def finish(self):
        conv = self.conv
        ab = self.ab
        for sub in self.fx_subs:
            sub.take_tracks()
        self.depth_rules()
        for ev in self.events:
            ab.add(ev)
        for ge in self.group_effects:
            ab.add(ge)
        if not self.main:
            ab.set("isComponent", True)
            clipped, collapsed = self.clip_uses
            ab.set("clip", True if clipped or not collapsed else False)
        else:
            ab.set("clip", True)
            if conv.bg:
                bg = list(getattr(self.comp, "bg_color", [0, 0, 0]))[:3]
                fill = E("Fill", id=conv.ids.key("comp", self.kid, "bg"), name="Background")
                fill.add(E("SolidColor", id=conv.ids.key("comp", self.kid, "bgc"), name="Color", colorValue=argb(bg, 1.0)))
                ab.add(fill)
        ab.add(self.anim.element())
        sm_id = conv.ids.key("comp", self.kid, "sm")
        st_id = conv.ids.key("comp", self.kid, "state")
        sm = E("StateMachine", id=sm_id, name="State Machine 1")
        layer = sm.add(E("StateMachineLayer", id=conv.ids.key("comp", self.kid, "sml"), name="Layer 1"))
        layer.add(E("AnyState", x=200.0, y=-120.0))
        layer.add(E("ExitState", x=400.0, y=-120.0))
        entry = layer.add(E("EntryState", x=0.0, y=0.0))
        entry.add(E("StateTransition", stateToId=st_id))
        layer.add(E("AnimationState", id=st_id, x=200.0, y=0.0, animationId=self.anim.id))
        ab.add(sm)
        if self.main:
            # a precomp's artboard plays through NestedRemapAnimation; a default state machine there would replay its
            # animation from 0 on every advance of a Luau instance (measured: ae_plane_artboard lost AE's time)
            ab.set("defaultStateMachineId", sm_id)


# ====================================================================== one layer
class LayerBuild:
    def __init__(self, cb, L):
        self.cb, self.L = cb, L
        self.conv = cb.conv
        self.tl = cb.tl
        self.anim = cb.anim
        self.name = clean(L.name)
        self.scope = cb.scope
        st = float(getattr(L, "stretch", 100.0) or 100.0)
        self.stretch = st / 100.0
        self.blend, unknown = blend_of(L)
        if unknown:
            self.note("approx", self.name, f"blend mode {unknown} is not in Rive: "
                                           f"{'normal' if self.blend == 'srcOver' else self.blend + ' (nearest)'}")
        self.copy_suffix = []
        self.colors = []          # ("solid", SolidColor E) / ("stop", GradientStop E)
        self.drawables = []

    # ---------------------------------------------------------------- utilities
    def note(self, kind, what, msg):
        self.conv.report.add(self.scope, kind, f"{self.name} · {what}" if what != self.name else what, msg)

    def id(self, *parts):
        suffix = tuple(f"copy{s}" for s in self.copy_suffix)
        return self.conv.ids.key("comp", self.cb.kid, "L", self.L.id, *parts, *suffix)

    def prop(self, p, what):
        return AProp(self.tl, p, self.L, what=f"{self.name} · {what}", stretch=self.stretch)

    def put(self, el, name, ap, dim=None, a=1.0, b=0.0, default=0.0, force=False):
        """static attribute or keys of one Rive property from an AProp (value -> a*v + b)"""
        if ap is None or ap.p is None:
            return
        if ap.animated:
            v0 = _c(ap.at(0.0), dim) * a + b
            el.set(name, float(v0))
            self.anim.put(el.id, el.tag, name, ap.rive_keys(dim, a, b))
            return
        v = ap.static(dim)
        if isinstance(v, list):
            v = v[0]
        v = tonum(v) * a + b
        if force or abs(v - default) > 1e-9:
            el.set(name, float(v))

    def put_dense(self, el, name, dense, hold=False):
        if not dense:
            return
        vals = [v for _, v in dense]
        el.set(name, float(vals[0]))
        if max(vals) - min(vals) < 1e-9:
            return
        if hold:
            keys, last = [], None
            for f, v in dense:
                if last is None or abs(v - last) > 1e-9:
                    keys.append((f, v, "hold", None))
                    last = v
            self.anim.put(el.id, el.tag, name, keys)
        else:
            self.anim.put(el.id, el.tag, name, reduce_track(dense))

    def clone(self, el, tag):
        """deep copy of an element tree with fresh ids (keys duplicated)"""
        import copy
        mapping = {}

        def cp(e):
            n = E(e.tag, id=None, name=e.name)
            n.attrs = dict(e.attrs)
            n.comments = list(e.comments)
            if e.id is not None:
                n.id = self.conv.ids.new()
                mapping[e.id] = n.id
            n.children = [cp(c) for c in e.children]
            return n
        out = cp(el)
        for (oid, pk) in list(self.anim.order):
            if oid in mapping:
                tr = self.anim.tracks[(oid, pk)]
                k = (mapping[oid], pk)
                self.anim.tracks[k] = copy.deepcopy(tr)
                self.anim.order.append(k)
        for e in out.iter():
            for a in ("sourceId", "styleId"):
                if a in e.attrs and e.attrs[a] in mapping:
                    e.attrs[a] = mapping[e.attrs[a]]
        return out

    def drop_tracks(self, ids):
        self.anim.order = [k for k in self.anim.order if k[0] not in ids]
        for k in list(self.anim.tracks):
            if k[0] in ids:
                del self.anim.tracks[k]

    def tprop(self, mn):
        tr = self.L.transform
        return tr.property(mn) if tr is not None else None

    # ---------------------------------------------------------------- frame (transform) node
    def frame(self, ghost):
        L = self.L
        node = E("Node", id=self.id("frame", ghost), name=self.name)
        pos = self.tprop("ADBE Position")
        sep = False
        try:
            sep = bool(pos.dimensions_separated)
        except Exception:
            pass
        if sep:
            self.put(node, "x", self.prop(self.tprop("ADBE Position_0"), "x position"), None)
            self.put(node, "y", self.prop(self.tprop("ADBE Position_1"), "y position"), None)
        else:
            ap = self.prop(pos, "position")
            self.put(node, "x", ap, 0)
            self.put(node, "y", ap, 1)
        self.put(node, "rotation", self.prop(self.tprop("ADBE Rotate Z"), "rotation"), None, math.pi / 180)
        sc = self.prop(self.tprop("ADBE Scale"), "scale")
        self.put(node, "scaleX", sc, 0, 0.01, default=1.0)
        self.put(node, "scaleY", sc, 1, 0.01, default=1.0)
        if ghost == 0:
            self.check_3d()
        anc = self.prop(self.tprop("ADBE Anchor Point"), "anchor point")
        if anc.animated or abs(tonum(anc.static(0))) > 1e-9 or abs(tonum(anc.static(1))) > 1e-9:
            inner = E("Node", id=self.id("anchor", ghost), name=self.name + " · anchor")
            self.put(inner, "x", anc, 0, -1.0)
            self.put(inner, "y", anc, 1, -1.0)
            node.add(inner)
            return node, inner
        return node, node

    def check_3d(self):
        # 3D layers (camera, X/Y rotations, orientation, Z) are projected in plane()
        L = self.L
        try:
            if int(L.auto_orient) == 4214 and three.is3d(L):
                self.note("converted", self.name, "orient towards camera → part of the 3D projection")
            elif int(L.auto_orient) not in (4212, 0):
                self.note("approx", self.name, "auto-orient along the path is not converted")
        except Exception:
            pass

    # ---------------------------------------------------------------- content
    def content(self, space):
        L, cb = self.L, self.cb
        kind = type(L).__name__
        matte_source = cb.is_matte(L) and not cb.drawn(L)
        bm = getattr(getattr(L, "blending_mode", None), "name", "") or ""
        if not matte_source and bm.startswith(("SILHOUET", "STENCIL")):
            # stencil / silhouette modes cut the layers BELOW with this layer's alpha and show nothing themselves
            # (broadcast-test: the skull's silhouette, zoomed, punches through the whole shot). Its geometry becomes a clip
            # that CompBuild.build() puts around every layer placed after it (Rive: later siblings are below)
            geo = space.add(E("Node", id=self.id("stencil"), name=f"{self.name} · {bm.lower()}"))
            self.emit_drawables(geo)
            ids = (self.id("stencilsrc"), self.id("stencilsrcinv"))
            silhouette = bm.startswith("SILHOUET")
            self.make_matte_source(geo, ids=ids, want_inverted=silhouette)
            cb.pending_stencil = (ids[1] if silhouette else ids[0], silhouette)
            cb.stencils.append(cb.pending_stencil)
            self.note("converted", self.name, f"blend mode {bm} → clip of every layer below "
                                              f"({'luma treated as alpha, ' if 'LUMA' in bm else ''}not drawn itself)")
            return
        if not matte_source and cb.matte_empty(L):
            self.note("converted", self.name, "its track matte lets nothing through (a null layer, directly or through "
                                              "the matte's own matte): not drawn, as in AE")
            if not cb.is_matte(L):
                return
            matte_source = True                   # still the geometry other layers clip with
        # AE 2023+: a matte layer whose eye is on is drawn AND clips (rig-test: a white solid, bubble shapes)
        visible_matte = cb.is_matte(L) and not matte_source
        has_children = bool(cb.children.get(L.index))
        plan = self.effects_plan() if not matte_source else {"wrappers": [], "colors": [], "comments": [], "ramps": []}
        holder = space
        if not matte_source and plan.get("copyfrom") is not None:
            self.note("approx", self.name, f"Compound Arithmetic 'Copy' from '{clean(plan['copyfrom'].name)}': it "
                                           f"repeats that layer's pixels — not drawn")
            if not cb.is_matte(L):
                return
            matte_source, visible_matte = True, False
            plan = {"wrappers": [], "colors": [], "comments": [], "ramps": []}
        # track matte: a node clipped by the matte layer's geometry
        m = cb.matte_of(L) if not matte_source else None
        if m is not None:
            mnode = E("Node", id=self.id("matte"), name=self.name + " · matte")
            holder.add(mnode)
            clip_id = self.matte_clip(m)
            if clip_id:
                mnode.add(E("ClippingShape", id=self.id("mclip"), name="Track matte", sourceId=clip_id[0],
                            fillRule=clip_id[1]))
            holder = mnode
        for fx in reversed(plan["wrappers"]):
            node, inner = self.transform_effect(fx)
            holder.add(node)
            holder = inner
        if plan.get("setmattes"):
            holder = self.set_matte_clips(holder, plan["setmattes"])
        if matte_source:
            own = E("Node", id=self.id("mattesrc"), name=self.name + " · matte source")
            holder.add(own)
            self.emit_drawables(own)
            self.wipe_matte(own)                  # Linear Wipe on a solid used as a matte: the wiped geometry
            self.plane(plan)                      # a 3D matte clips through its projection too
            self.make_matte_source(own)
            return
        op = self.prop(self.tprop("ADBE Opacity"), "opacity")
        in_f = int(round(float(L.in_point) * self.tl.fps))
        out_f = int(round(float(L.out_point) * self.tl.fps))
        need_in, need_out = in_f > 0, out_f < self.tl.nframes
        masks = self.mask_items()
        draw_into = holder
        content_node = None
        if op.animated or abs(tonum(op.static(None, 100)) - 100) > 1e-6 or need_in or need_out or masks or visible_matte:
            if (not has_children and not masks and not plan["wrappers"] and m is None and not visible_matte
                    and self._frame_owns(space)):
                target = space                    # the layer's own node: `ae pull` reads its opacity there
            else:
                content_node = E("Node", id=self.id("content"), name=self.name + " · content")
                holder.add(content_node)
                target = draw_into = content_node
            self.opacity_window(target, op, in_f, out_f, need_in, need_out)
        self.emit_drawables(draw_into)
        self.plane(plan)
        if masks:
            self.masks(masks, content_node, holder)
        for fx in plan["ramps"]:
            self.apply_ramp(fx)
        if plan["colors"]:
            self.apply_colors(plan["colors"])
        for fx in plan.get("blurs", []):
            self.blur(fx)
        for fx in plan.get("shadows", []):
            self.drop_shadow(fx, draw_into)
        for fx in plan.get("glows", []):
            self.glow(fx, draw_into)
        if plan.get("wgsl"):
            self.wgsl_layer(plan["wgsl"], bool(masks))
        if plan["comments"]:
            first = next((e for e in self.drawables), None)
            if first is not None:
                first.comments += plan["comments"]
            else:
                self.note("approx", self.name, "effects kept as comments could not be attached (no drawable)")
        if visible_matte and content_node is not None:
            # the copy keeps its keys (in/out window…) under fresh ids; the wrapper takes the reserved clip id
            own = E("Node", id=self.id("mattesrc"), name=self.name + " · matte source")
            own.add(self.clone(content_node, "mattesrc"))
            holder.add(own)
            self.make_matte_source(own)
            self.note("converted", self.name, "visible track matte layer: drawn, and a copy of its geometry clips")

    # ---------------------------------------------------------------- 3D / Corner Pin
    def source_size(self):
        src = getattr(self.L, "source", None)
        w = float(getattr(src, "width", 0) or 0) if src is not None else 0.0
        h = float(getattr(src, "height", 0) or 0) if src is not None else 0.0
        return (w or float(self.cb.comp.width)), (h or float(self.cb.comp.height))

    def plane(self, plan):
        """a 3D layer seen through the camera, and/or a Corner Pin: the layer is drawn with its flattened 2D transform
        F(t) (what AE gives its 2D children); H'(t) = H(t) · F(t)⁻¹ moves those artboard points to where AE draws them.
        Shapes: exact, by the Luau path effect ae_project (a GroupEffect targeted by each paint). Images, text and
        precomps: the affine that matches H' at the anchor (two nested nodes, rotation · scale · rotation)."""
        L, tl, cb = self.L, self.tl, self.cb
        cpin = plan.get("cornerpin")
        d3 = three.is3d(L)
        if not (d3 or cpin is not None) or not self.drawables:
            return
        eng = self.conv.engine
        pr = eng.projector(cb.comp, None)
        f0 = max(0, int(math.floor(float(L.in_point) * tl.fps + 1e-6)))
        f1 = min(tl.nframes, int(math.ceil(float(L.out_point) * tl.fps - 1e-6)))
        if f1 < f0:
            return
        if cpin is not None:
            w, h = self.source_size()
            quad = [(0.0, 0.0), (w, 0.0), (0.0, h), (w, h)]
            pins = [cpin.property(f"ADBE Corner Pin-000{i}") for i in (1, 2, 3, 4)]
        anc_p = self.tprop("ADBE Anchor Point")
        dense = {k: [] for k in range(9)}
        gdense = {k: [] for k in range(9)}      # G = F⁻¹·H: the same map expressed in layer space (images)
        aff = []
        behind = 0
        last = None
        for f in range(f0, f1 + 1):
            t = f / tl.fps
            Fa = eng.layer_matrix(L, cb.comp, None, t)
            F = three.affine3(Fa)
            T = pr.homography(L, t) if d3 else F
            if cpin is not None:
                dst = []
                for p in pins:
                    v = unwrap(eng.value(p, L, cb.comp, t)) if p is not None else [0.0, 0.0]
                    dst.append((tonum(v[0]), tonum(v[1])))
                P = three.quad_homography(quad, dst)
                if P is not None:
                    T = three.h_mul(T, P)
            Fi = three.h_inv(F)
            if Fi is None:
                if last is None:
                    continue
                Hp, X = last
            else:
                Hp = three.h_mul(T, Fi)
                av = unwrap(eng.value(anc_p, L, cb.comp, t)) if anc_p is not None else [0.0, 0.0]
                ax, ay = tonum(av[0]), tonum(av[1])
                wx, wy = geom.apply(Fa, (ax, ay))
                wd = Hp[2][0] * wx + Hp[2][1] * wy + Hp[2][2]
                if wd <= 1e-9:
                    behind += 1
                    wd = abs(wd) or 1.0
                Hp = [[v / wd for v in row] for row in Hp]
                J = three.jacobian(three.h_mul(Hp, F), ax, ay)
                X = geom.mul(geom.invert(Fa) or geom.IDENT, J)
            last = (Hp, X)
            G = three.h_mul(three.h_inv(F) or three.affine3(geom.IDENT), three.h_mul(Hp, F))
            for k in range(9):
                dense[k].append((f, Hp[k // 3][k % 3]))
                gdense[k].append((f, G[k // 3][k % 3]))
            aff.append((f, X))
        if not aff:
            return
        # layer-space map per comp frame, for the masks (held outside the layer's window)
        gmap = {f: [[gdense[k][j][1] for k in range(r * 3, r * 3 + 3)] for r in range(3)]
                for j, (f, _X) in enumerate(aff)}
        self._plane_g = [gmap.get(min(max(f, aff[0][0]), aff[-1][0])) for f in range(tl.nframes + 1)]
        tol = (1e-6, 1e-6, 1e-3, 1e-6, 1e-6, 1e-3, 1e-9, 1e-9, 1e-6)
        if all(abs(v - (1.0 if k in (0, 4, 8) else 0.0)) < tol[k] for k in range(9) for _, v in dense[k]):
            return                          # flat, facing the default camera: the 2D transform is already exact
        paints, others = [], []
        for d in self.drawables:
            # a Text's glyphs take path effects too (measured, CLI 1.2.0): text stays text, in exact perspective
            if d.tag in ("Shape", "Node", "Text") and not any(e.tag in ("Image", "NestedArtboard", "Solo") for e in d.iter()):
                paints += [e for e in d.iter() if e.tag in ("Fill", "Stroke")]
            else:
                others.append(d)
        what = "3D layer" if d3 else "Corner Pin"
        if paints:
            ge = E("GroupEffect", id=self.id("plane"), name=f"{self.name} · {'3D' if d3 else 'Corner Pin'}")
            fx = ge.add(E("ScriptedPathEffect", id=self.id("plane", "fx"), name="ae_project",
                          scriptAssetId=self.conv.script("ae_project")))
            self.h_inputs(fx, "plane", dense)
            cb.group_effects.append(ge)
            for p in paints:
                p.add(E("TargetEffect", id=self.id("plane", "t", p.id), name="3D", targetId=ge.id))
            self.note("converted", self.name, f"{what} → exact perspective on its shapes (Luau ae_project, keyed "
                                              f"per frame)")
            if d3 and any(p.tag == "Stroke" for p in paints):
                self.note("approx", self.name, "3D: stroke thickness is not foreshortened (the outline is projected, "
                                               "the width stays uniform)")
            if d3 and type(L).__name__ == "TextLayer" and getattr(L, "three_d_per_char", False):
                self.note("approx", self.name, "per-character 3D: the text is projected as one plane")
        holder = getattr(self, "_draw_holder", None)
        for d in list(others):
            if d.tag in ("Image", "Solo") and holder is not None and d in holder.children:
                # images, videos, sequences: exact perspective by a mesh drawn in Luau (ae_plane_image)
                holder.children[holder.children.index(d)] = self.plane_image(d, gdense, what)
                others.remove(d)
            elif d.tag == "NestedArtboard" and holder is not None and d in holder.children:
                # a precomp: rendered flat into a canvas (as AE does), then mapped on the plane (ae_plane_artboard)
                sd = self.plane_artboard(d, gdense, what, aff)
                if sd is not None:
                    holder.children[holder.children.index(d)] = sd
                    others.remove(d)
        for d in others:
            if holder is None or d not in holder.children:
                continue
            outer = E("Node", id=self.id("plane", "a", d.id), name=f"{self.name} · {what}")
            inner = outer.add(E("Node", id=self.id("plane", "b", d.id), name=f"{self.name} · {what} ·"))
            holder.children[holder.children.index(d)] = outer
            inner.add(d)
            tr = {n: [] for n in ("x", "y", "rotation", "scaleX", "scaleY", "r2")}
            prev = None
            for f, X in aff:
                a, b, c, dd, e, ff = X
                t1, sx, sy, t2 = three.svd_nodes(a, b, c, dd)
                if prev is not None:
                    best = None
                    for k in range(-2, 3):
                        c1 = geom.unwrap(prev[0], t1 + k * math.pi)
                        c2 = geom.unwrap(prev[1], t2 + k * math.pi)
                        cost = abs(c1 - prev[0]) + abs(c2 - prev[1])
                        if best is None or cost < best[0]:
                            best = (cost, c1, c2)
                    t1, t2 = best[1], best[2]
                prev = (t1, t2)
                for n, v in (("x", e), ("y", ff), ("rotation", t1), ("scaleX", sx), ("scaleY", sy), ("r2", t2)):
                    tr[n].append((f, v))
            for n in ("x", "y", "rotation", "scaleX", "scaleY"):
                self.put_dense(outer, n, tr[n])
            self.put_dense(inner, "rotation", tr["r2"])
            self.note("approx", self.name, f"{what}: {d.tag} drawn with the affine closest to the perspective at its "
                                           f"anchor (exact on shapes only)")
        if behind:
            self.note("approx", self.name, f"{what}: behind the camera on {behind} frame(s) — AE hides it, it stays drawn")
        if self.mask_items():
            self.note("converted", self.name, f"{what}: masks projected too (vertices keyed per frame)")

    def h_inputs(self, el, tag, dense):
        """h11…h33 ScriptInputNumber children, keyed per frame (tolerance relative to each entry's magnitude)"""
        for k, n in enumerate(("h11", "h12", "h13", "h21", "h22", "h23", "h31", "h32", "h33")):
            inp = el.add(E("ScriptInputNumber", id=self.id(tag, el.id, n), name=n))
            vals = [v for _, v in dense[k]]
            rng = max(abs(v) for v in vals) or 1.0
            inp.set("propertyValue", float(vals[0]))
            if max(vals) - min(vals) > 1e-12:
                self.anim.put(inp.id, "ScriptInputNumber", "propertyValue", reduce_track(dense[k], tol=rng * 2e-5))

    def plane_image(self, d, gdense, what):
        """Image / Solo of frames -> ScriptedDrawable ae_plane_image (same rectangle, same asset names)"""
        conv = self.conv
        names = {e.id: e.name for e in conv.assets if e.tag == "ImageAsset"}
        w, h = self.source_size()
        sd = E("ScriptedDrawable", id=self.id("pimg", d.id), name=f"{self.name} · {what}",
               scriptAssetId=conv.script("ae_plane_image"))
        sd.comments = list(getattr(d, "comments", []))
        img = sd.add(E("ScriptInputString", id=self.id("pimg", d.id, "image"), name="image"))
        if d.tag == "Image":
            img.set("propertyValue", names.get(d.attrs.get("assetId"), ""))
        else:
            by_img = {c.id: names.get(c.attrs.get("assetId"), "") for c in d.children}
            img.set("propertyValue", by_img.get(d.attrs.get("activeComponentId"), ""))
            tr = self.anim.tracks.get((d.id, prop_key("Solo", "activeComponentId")))
            if tr is not None:
                keys = [(k[0], by_img.get(k[1], ""), "hold", None) for k in tr.keys]
                self.anim.put(img.id, "ScriptInputString", "propertyValue", keys, kind="string")
            self.drop_tracks({d.id})
        sd.add(E("ScriptInputNumber", id=self.id("pimg", d.id, "w"), name="width", propertyValue=float(w)))
        sd.add(E("ScriptInputNumber", id=self.id("pimg", d.id, "h"), name="height", propertyValue=float(h)))
        sd.add(E("ScriptInputString", id=self.id("pimg", d.id, "blend"), name="blend",
                 propertyValue=self.blend or "srcOver"))
        self.h_inputs(sd, "pimg", gdense)
        self.note("converted", self.name, f"{what}: {'image' if d.tag == 'Image' else 'video / sequence'} in exact "
                                          "perspective (Luau ae_plane_image, 16×16 mesh)")
        return sd

    def plane_artboard(self, d, gdense, what, aff):
        conv = self.conv
        cb2 = next((cb for cb in conv.order if cb.ab.id == d.attrs.get("artboardId")), None)
        remap = next((c for c in d.children if c.tag == "NestedRemapAnimation"), None)
        if cb2 is None or remap is None:
            return None
        D = cb2.anim.duration / float(cb2.anim.fps)
        # canvas resolution: the largest on-screen magnification of the plane over the comp (at the anchor)
        mag = 1.0
        for f, X in aff:
            m = geom.mul(conv.engine.layer_matrix(self.L, self.cb.comp, None, f / self.tl.fps), X)
            _t1, sx, sy, _t2 = three.svd_nodes(m[0], m[1], m[2], m[3])
            mag = max(mag, abs(sx), abs(sy))
        res = min(3.0, math.ceil(mag * 4) / 4.0)
        sd = E("ScriptedDrawable", id=self.id("pab", d.id), name=f"{self.name} · {what}",
               scriptAssetId=conv.script("ae_plane_artboard"))
        sd.comments = list(getattr(d, "comments", []))
        sd.add(E("ScriptInputArtboard", id=self.id("pab", d.id, "ab"), name="artboard", artboardId=cb2.ab.id))
        sd.add(E("ScriptInputString", id=self.id("pab", d.id, "anim"), name="animation", propertyValue=cb2.anim.name))
        tin = sd.add(E("ScriptInputNumber", id=self.id("pab", d.id, "time"), name="time",
                       propertyValue=float(remap.attrs.get("time", 0.0) or 0.0) * D))
        tr = self.anim.tracks.get((remap.id, prop_key("NestedRemapAnimation", "time")))
        if tr is not None:
            self.anim.put(tin.id, "ScriptInputNumber", "propertyValue",
                          [(k[0], k[1] * D, k[2], k[3]) for k in tr.keys])
        self.drop_tracks({remap.id})
        sd.add(E("ScriptInputNumber", id=self.id("pab", d.id, "w"), name="width", propertyValue=float(cb2.comp.width)))
        sd.add(E("ScriptInputNumber", id=self.id("pab", d.id, "h"), name="height", propertyValue=float(cb2.comp.height)))
        sd.add(E("ScriptInputNumber", id=self.id("pab", d.id, "res"), name="resolution", propertyValue=res))
        sd.add(E("ScriptInputString", id=self.id("pab", d.id, "blend"), name="blend",
                 propertyValue=self.blend or "srcOver"))
        self.h_inputs(sd, "pab", gdense)
        self.note("converted", self.name, f"{what}: precomp rendered flat then mapped in exact perspective (Luau "
                                          f"ae_plane_artboard, canvas ×{res:g})")
        return sd

    def _frame_owns(self, space):
        """the layer's frame node is where its content lands (no anchor node in between)"""
        f = self.cb.frames.get(self.L.index)
        return f is space

    def opacity_window(self, target, op, in_f, out_f, need_in, need_out):
        N = self.tl.nframes
        if not (need_in or need_out):
            self.put(target, "opacity", op, None, 0.01, default=1.0)
            return
        if op.animated:
            self.put(target, "opacity", op, None, 0.01, default=1.0)
            vis = E("Node", id=self.id("inout"), name=self.name + " · in/out")
            target.add(vis)
            keys = [(0, 0.0 if need_in else 1.0, "hold", None)]
            if need_in:
                keys.append((in_f, 1.0, "hold", None))
            if need_out:
                keys.append((max(out_f, 0), 0.0, "hold", None))
            vis.set("opacity", keys[0][1])
            self.anim.put(vis.id, "Node", "opacity", keys)
            self._window_target = vis
            return
        o = tonum(op.static(None, 100)) / 100.0
        keys = [(0, 0.0 if need_in else o, "hold", None)]
        if need_in:
            keys.append((in_f, o, "hold", None))
        if need_out:
            keys.append((max(out_f, 0), 0.0, "hold", None))
        target.set("opacity", keys[0][1])
        self.anim.put(target.id, target.tag, "opacity", keys)
        if in_f >= N or out_f <= 0:
            self.note("info", self.name, "layer outside the comp's time range: never visible")

    def emit_drawables(self, holder):
        """the layer's own drawing, by layer type"""
        L = self.L
        kind = type(L).__name__
        vis = getattr(self, "_window_target", None)
        if vis is not None:
            holder = vis
        self._draw_holder = holder
        before = len(holder.children)
        self._draw_before = before
        if kind == "ShapeLayer":
            root = L.property("ADBE Root Vectors Group")
            if root is not None:
                ShapeBuilder(self).build(holder, root, self.blend)
        elif kind == "TextLayer":
            self.text(holder)
        elif kind == "AVLayer":
            src = getattr(L, "source", None)
            if src is not None and hasattr(src, "layers"):
                self.precomp(holder, src)
            elif src is not None:
                self.footage(holder, src)
        self.drawables = [c for c in holder.children[before:]]
        if self.blend and self.blend != "srcOver" and kind != "ShapeLayer":
            for c in self.drawables:
                if c.tag in ("Shape", "Image", "Text", "NestedArtboard"):
                    c.set("blendModeValue", self.blend)
                elif c.tag == "Solo":
                    for img in c.children:
                        img.set("blendModeValue", self.blend)
        if len(self.drawables) > 1:
            op = self.prop(self.tprop("ADBE Opacity"), "opacity")
            if op.animated or abs(tonum(op.static(None, 100)) - 100) > 1e-6:
                self.note("approx", self.name, "layer opacity applies to each shape (overlaps inside the layer show through)")

    # ---------------------------------------------------------------- layer kinds
    def audio(self):
        """audio layer -> AudioAsset + AudioEvent fired by a timeline callback at the layer's in point"""
        L, conv = self.L, self.conv
        if not conv.with_audio:
            return
        src = L.source
        t_in, t_out = max(0.0, float(L.in_point)), min(self.tl.duration, float(L.out_point))
        s0, s1 = self.source_time(t_in), self.source_time(t_out)
        dur = float(getattr(src, "duration", 0) or 0)
        aid = None
        if s0 > 0.02 or (dur and s1 < dur - 0.02):
            # Rive plays an AudioAsset whole from its trigger: cut the file to what the AE layer plays
            aid = conv.trimmed_audio(src, max(0.0, s0), s1 if not dur else min(dur, s1))
        if aid is None:
            aid = conv.audio_asset(src)
        if aid is None:
            return
        self.audio_event(aid, t_in, "audio layer")

    def audio_event(self, aid, t_in, what):
        ev = E("AudioEvent", id=self.id("audio"), name=self.name, assetId=aid)
        self.cb.events.append(ev)
        f = int(round(t_in * self.tl.fps))
        if f < self.tl.nframes:
            self.anim.put(ev.id, "AudioEvent", "trigger", [(f, None, "hold", None)], kind="callback")
        if abs(self.stretch - 1) > 1e-3 or getattr(self.L, "time_remap_enabled", False):
            self.note("approx", self.name, f"{what}: time stretch / remap not applied to the sound")
        self.note("converted", self.name, f"{what} → AudioAsset + AudioEvent at the in point (plays in the Rive "
                                          "previewer, not in --screenshot)")

    def source_time(self, t):
        """comp time -> the layer's source time (start time, stretch, time remap)"""
        L = self.L
        if getattr(L, "time_remap_enabled", False):
            ap = self.prop(L.property("ADBE Time Remapping"), "time remap")
            return tonum(unwrap(ap.at(t)))
        sf = self.stretch if abs(self.stretch) > 1e-9 else 1.0
        return (t - float(L.start_time)) / sf

    def footage(self, holder, src):
        L = self.L
        ms = getattr(src, "main_source", None)
        kind = type(ms).__name__
        w, h = float(getattr(src, "width", 0) or 0), float(getattr(src, "height", 0) or 0)
        if kind == "SolidSource":
            color = list(getattr(ms, "color", [0.5, 0.5, 0.5]))
            self.rect_drawable(holder, w, h, color, "solid")
            return
        if kind == "FileSource":
            f = (getattr(ms, "file", "") or "").lower()
            if not self.conv.with_video and f.endswith(VIDEO_EXT) and not getattr(ms, "is_still", False):
                self.note("info", self.name, "video left out (--no-video)")
                self._image_done = True
                return
            fn = self.pending_color_fn()
            if self.moving(src) and self.sequence(holder, src, fn, w, h):
                self._image_done = True
                return
            res = self.conv.image_asset(src, L, transform=fn, tag=("fx" + str(L.id)) if fn else "")
            if res:
                aid, iw, ih = res
                img = E("Image", id=self.id("image"), name=self.name, assetId=aid, originX=0.0, originY=0.0)
                if w and iw and abs(iw - w) > 0.5:
                    img.set("scaleX", w / iw).set("scaleY", h / ih if ih else 1.0)
                holder.add(img)
                self._image_done = True
                return
            if w and h:
                self.rect_drawable(holder, w, h, [0.5, 0.5, 0.5], "missing footage")
            return
        self.note("unsupported", self.name, f"footage source {kind}: placeholder")
        if w and h:
            self.rect_drawable(holder, w, h, [0.5, 0.5, 0.5], "placeholder")

    def moving(self, src):
        ms = src.main_source
        if getattr(ms, "is_still", False):
            return False
        info = self.conv.media_info(src)
        return info is not None and (info[3] or len(info[5]) > 1)

    def sequence(self, holder, src, fn, w, h):
        """video / image sequence -> a Solo of its frames, the visible one keyed per comp frame (KeyFrameId, hold);
        a video's own sound -> a separate WAV cut to the part the layer plays + AudioEvent"""
        L, conv, tl = self.L, self.conv, self.tl
        path, sfps, n, video, _alpha, _files = conv.media_info(src)
        loops = max(1, int(getattr(src.main_source, "loop", 1) or 1))
        f0 = max(0, int(math.ceil(float(L.in_point) * tl.fps - 1e-6)))
        f1 = min(tl.nframes, int(math.ceil(float(L.out_point) * tl.fps - 1e-6)) - 1)
        if f1 < f0:
            return False
        step = max(1.0, sfps / conv.media_fps) if conv.media_fps else 1.0

        def index(t):
            i = int(math.floor(self.source_time(t) * sfps + 1e-4))
            if loops > 1 and n <= i < n * loops:
                i %= n
            i = min(max(i, 0), n - 1)
            return int(math.floor(i / step) * step)
        frames = [(f, index(f / tl.fps)) for f in range(f0, f1 + 1)]
        tag = ("fx" + str(L.id)) if fn else ""
        # a colour effect is baked into the pixels: once per source frame, or per displayed frame when it is animated
        per_frame = fn is not None and self.color_animated([f for f, _ in frames])
        if per_frame:
            reqs = [(f"{i:05d}_{f:05d}", i, self.color_fn_at(f / tl.fps)) for f, i in frames]
            self.note("converted", self.name, "animated colour effect baked into each frame of the sequence")
        else:
            reqs = [(f"{i:05d}", i, fn) for i in sorted({i for _, i in frames})]
        assets = conv.media_frames(src, reqs, tag=tag)
        if not assets:
            return False
        solo = E("Solo", id=self.id("seq"), name=f"{self.name} · frames")
        ids = {}
        for k, _i, _fn in reqs:
            if k not in assets or k in ids:
                continue
            aid, iw, ih = assets[k]
            img = E("Image", id=self.id("seq", k), name=f"{self.name} {k}", assetId=aid, originX=0.0, originY=0.0)
            if w and iw and abs(iw - w) > 0.5:
                img.set("scaleX", w / iw).set("scaleY", h / ih if ih else 1.0)
            solo.add(img)
            ids[k] = img.id
        if not ids:
            return False
        keys, last = [], None
        for f, i in frames:
            v = ids.get(f"{i:05d}_{f:05d}" if per_frame else f"{i:05d}", last)
            if v is not None and v != last:
                keys.append((f, v, "hold", None))
                last = v
        solo.set("activeComponentId", keys[0][1])
        if len(keys) > 1:
            self.anim.put(solo.id, "Solo", "activeComponentId", keys, kind="id")
        holder.add(solo)
        what = "video" if video else "image sequence"
        folder = slug(os.path.splitext(clean(src.name))[0]) + (f"_{tag}" if tag else "")
        self.note("converted", self.name, f"{what} → {len(ids)} images in a Solo switched every frame "
                                          f"(assets/{folder}/)")
        if video and bool(getattr(L, "audio_enabled", True)) and bool(getattr(src, "has_audio", True)):
            t_in, t_out = max(0.0, float(L.in_point)), min(tl.duration, float(L.out_point))
            aid = conv.media_audio(src, max(0.0, self.source_time(t_in)), self.source_time(t_out)) if conv.with_audio else None
            if aid is not None:
                self.audio_event(aid, t_in, "video sound")
        return True

    def rect_drawable(self, holder, w, h, color, what):
        s = E("Shape", id=self.id("solid"), name=self.name)
        s.add(E("Rectangle", id=self.id("solid", "r"), name="Rectangle", width=w, height=h, originX=0.0, originY=0.0))
        f = s.add(E("Fill", id=self.id("solid", "f"), name="Fill"))
        sc = f.add(E("SolidColor", id=self.id("solid", "c"), name="Color", colorValue=argb(color, 1.0)))
        self.colors.append(("solid", sc))
        holder.add(s)

    def instance_overrides(self, src):
        """Essential Properties this precomp layer overrides (plus those of the variant being built that act
        deeper in `src`) -> {id(source property): (fn(t), keyed, owner comp id, signature)}"""
        from .aexpr import _children
        reach = self.conv.reachable(src)
        # an override reaches the comps that hold its property, and those that READ it through comp("…"): AE renders
        # them in the instance's context (broadcast-test's zootrope: each cone_* precomp picks its view from
        # comp("Glaces_Animation")'s dropdown, which every Glaces_Animation instance overrides)
        refs = self.conv.comp_refs(src)
        names = {c.id: clean(c.name) for c in self.conv.project.compositions}
        out = {k: o for k, o in aexpr.OVERRIDES.items() if o[2] in reach or names.get(o[2]) in refs}
        try:
            g = self.L.property("ADBE Layer Overrides")
        except Exception:
            g = None
        st, sf = float(self.L.start_time), (self.stretch or 1.0)
        remapped = bool(getattr(self.L, "time_remap_enabled", False))
        for p in (_children(g) if g is not None else []):
            try:
                keyed = bool(p.is_time_varying)
                if not keyed and not p.is_modified:
                    continue                      # untouched: the instance shows the precomp's own value
                s = p.essential_property_source
            except Exception:
                continue
            owner = _owner_comp(s)
            if s is None or not hasattr(s, "value") or owner is None or owner.id not in reach:
                self.note("approx", self.name, f"Essential Property '{clean(p.name)}' (media replacement or "
                                               f"unresolved source) not applied")
                continue
            if keyed:
                # its keys live on this layer, in this comp's time: child time t -> st + t * stretch
                fn = (lambda t, p=p: p.value_at_time(st + (t or 0.0) * sf))
                sig = (id(s), "keys", id(p))
                if remapped:
                    self.note("approx", self.name, f"keyed Essential Property '{clean(p.name)}' on a time-remapped "
                                                   f"layer: read along the layer's start, not through the remap")
            else:
                v = p.value
                fn = (lambda t, v=v: v)
                vs = to_js(v)
                sig = (id(s), tuple(round(tonum(x), 6) for x in vs) if isinstance(vs, list)
                       else (round(vs, 6) if isinstance(vs, float) else ("instance", id(p))))
            out[id(s)] = (fn, keyed, owner.id, sig)
            self.note("converted", self.name, f"Essential Property '{clean(p.name)}' overridden → its own variant "
                                              f"of the precomp's artboard")
        return out

    def precomp(self, holder, src):
        L, conv = self.L, self.conv
        cols = list(getattr(self, "_color_plan", None) or [])
        cb2 = conv.comp_build(src, overrides=self.instance_overrides(src), colors=cols)
        collapsed = bool(getattr(L, "collapse_transformation", False))
        if collapsed and any(three.is3d(x) for x in src.layers):
            self.note("approx", self.name, "collapsed precomp with 3D layers: AE shows them through THIS comp's "
                                           "camera; here the precomp is drawn flat with its own camera")
        cb2.clip_uses[1 if collapsed else 0] += 1
        ne = E("NestedArtboard", id=self.id("nested"), name=self.name, artboardId=cb2.ab.id)
        if collapsed and cb2.stencils:
            # AE: a collapsed precomp's blend modes act in THIS comp — its stencil / silhouette layers cut the layers
            # below the precomp layer too (broadcast-test's festival: the zoomed skull of Pl04-anim empties the green
            # background and the logo of the shot)
            self.cb.collapsed_stencil = (ne, cb2)
        remap = ne.add(E("NestedRemapAnimation", id=self.id("remap"), name="Time", animationId=cb2.anim.id))
        D = max(1e-6, cb2.anim.duration / float(cb2.anim.fps))
        # the nested time lands on its frames as 1.99999…: hold keys inside the precomp (a mask keyed frame by frame)
        # then show the previous frame. A hundredth of a precomp frame ahead fixes it, invisibly (FX Monster Shape_01)
        bias = 0.01 / float(cb2.anim.fps) / D
        if getattr(L, "time_remap_enabled", False):
            ap = self.prop(L.property("ADBE Time Remapping"), "time remap")
            self.put(remap, "time", ap, None, 1.0 / D, bias, force=True)
            self.note("converted", self.name, "time remap → NestedRemapAnimation.time")
        else:
            st, sf = float(L.start_time), self.stretch
            fps = self.tl.fps
            T = self.tl.duration
            if abs(sf) < 1e-9:
                sf = 1.0
            if sf < 0:
                self.note("approx", self.name, "reversed layer (negative stretch): played forward")
                sf = abs(sf)

            def frac(t):
                return (t - st) / sf / D + bias
            t0 = max(0.0, st)
            t1 = min(T, st + D * sf)
            if t1 <= t0:
                remap.set("time", float(max(0.0, min(1.0, frac(0.0)))))
            else:
                # (Posterize Time on a precomp layer does NOT step the precomp's own time: AE's renders of FX
                # Monster's Shape_01 match the unstepped time — 0.01 % against 0.9 % stepped)
                keys = [(t0 * fps, max(0.0, frac(t0)), "linear", None), (t1 * fps, min(1.0, frac(t1)), "hold", None)]
                remap.set("time", float(max(0.0, frac(0.0))))
                self.anim.put(remap.id, "NestedRemapAnimation", "time", keys)
        holder.add(ne)
        if cols:
            self.note("converted", self.name, "colour effect on a precomp layer → applied inside a variant of its "
                                              "artboard (animated parameters read at the precomp's own time)")

    def text(self, holder):
        L, conv = self.L, self.conv
        tp = L.text.property("ADBE Text Document") if L.text is not None else None
        if tp is None:
            return
        try:
            ks = list(tp.keyframes)
        except Exception:
            ks = []
        doc = ks[0].value if ks else tp.value
        o = aexpr.OVERRIDES.get(id(tp))
        if o is not None:
            doc = o[0](0.0)                       # the instance's Essential Property (keyed text: first value)
            ks = []
        size = float(doc.font_size or 12)
        lead = float(doc.leading) if (doc.leading and not getattr(doc, "auto_leading", False)) else size * 1.2
        style = self.text_style(doc, (), lead)
        txt = clean(doc.text).replace("\r\n", "\n").replace("\r", "\n").replace("\x03", "\n")
        if getattr(doc, "all_caps", False):
            txt = txt.upper()
        j = getattr(doc, "justification", "")
        just = getattr(j, "name", None) or str(j)     # py-aep's enum prints its number (7415), not CENTER_JUSTIFY
        align, ox = ("center", 0.5) if "CENTER" in just and "FULL" not in just else (("right", 1.0) if "RIGHT_JUSTIFY" in just else ("left", 0.0))
        el = E("Text", id=self.id("text"), name=self.name, alignValue=align)
        # AE baseline shift (character setting, often left in the user's defaults: 24 px in AE 26) lifts the
        # glyphs off the layer's baseline — measured with sourceRectAtTime in AE, it was the whole offset
        bshift = float(getattr(doc, "baseline_shift", 0) or 0)
        if getattr(doc, "box_text", False) and doc.box_text_size:
            bw, bh = float(doc.box_text_size[0]), float(doc.box_text_size[1])
            bx, by = float(doc.box_text_pos[0]), float(doc.box_text_pos[1])
            el.set("sizingValue", "fixed").set("width", bw).set("height", bh + size)
            el.set("originValue", "baseline").set("x", bx).set("y", by + AE_BOX_BASELINE * size - bshift)
            self.note("approx", self.name, "paragraph text: first baseline placed at box top + 0.743 × size (AE constant "
                                           "measured on Montserrat)")
        else:
            el.set("sizingValue", "autoWidth").set("originValue", "baseline").set("originX", ox)
            if abs(bshift) > 1e-6:
                el.set("y", -bshift)
        if abs(bshift) > 1e-6:
            self.note("converted", self.name, f"baseline shift {bshift:g} → text raised by it (one value for the whole "
                                              "text: per-character shifts are not in Rive)")
        el.add(style)
        animated_text = len(ks) > 1 or conv.engine.has_expr(tp)
        runs = [] if animated_text else self.style_runs(doc)
        if align != "left":
            # AE centres (or right-aligns) a line without its trailing spaces; Rive keeps them, shifting the line by
            # half a space (6-7 px on Mh_LST_Resp_Drinking's paragraph, every line ending in " \n")
            txt = re.sub(r"[ \t\u00a0]+(?=\n|$)", "", txt)
            clean_runs = []
            for k, (seg, src, key) in enumerate(runs):
                seg = re.sub(r"[ \t\u00a0]+(?=\n)", "", seg)
                if k + 1 == len(runs) or runs[k + 1][0].startswith("\n"):
                    seg = seg.rstrip(" \t\u00a0")
                if seg:
                    clean_runs.append((seg, src, key))
            runs = clean_runs
        if len(runs) > 1:
            # several character styles in one text (a line in Futura Book, the next in Medium…): one TextStylePaint
            # and one TextValueRun per run, in order (Mh_LST_Resp_Drinking_01)
            styles = {runs[0][2]: style}
            run = None
            for k, (seg, src, key) in enumerate(runs):
                st = styles.get(key)
                if st is None:
                    st = styles[key] = el.add(self.text_style(src, ("run", len(styles)), lead))
                r = el.add(E("TextValueRun", id=self.id("run", *([k] if k else [])), name=f"Run {k + 1}", styleId=st.id,
                             text=seg))
                run = run or r
            self.note("converted", self.name, f"{len(runs)} character-style runs → {len(styles)} text styles")
        else:
            run = el.add(E("TextValueRun", id=self.id("run"), name="Run", styleId=style.id, text=txt))
        if animated_text:
            ap = self.prop(tp, "source text")
            if ap.mode == "keys":
                keys = [(k.time * self.tl.fps, self._doc_text(k.value), "hold", None) for k in ap.keys]
            elif ap.mode == "frames":
                keys, last = [], None
                for f, v in enumerate(ap.frames):
                    s = str(unwrap(v))
                    if s != last:
                        keys.append((f, s, "hold", None))
                        last = s
            else:
                keys = []
                run.set("text", str(unwrap(ap.value)) if ap.value is not None else txt)
            if keys:
                run.set("text", keys[0][1])
                self.anim.put(run.id, "TextValueRun", "text", keys, kind="string")
                self.note("converted", self.name, f"source text changes → {len(keys)} text keys")
            if len(ks) > 1 and any(abs(float(k.value.font_size or 0) - size) > 1e-3 for k in ks):
                self.note("approx", self.name, "font size changes between source-text keys: first size kept")
        self._text_keyed = animated_text or len(runs) > 1
        self._text_font = (self._font_ps(doc), size, align)
        anims = L.text.property("ADBE Text Animators") if L.text is not None else None
        if anims is not None:
            self.text_animators(el, anims, txt, doc)
        po = L.text.property("ADBE Text Path Options") if L.text is not None else None
        try:
            pidx = int(tonum(po.property("ADBE Text Path").value)) if po is not None and po.property("ADBE Text Path") is not None else 0
        except Exception:
            pidx = 0
        if pidx > 0:
            try:
                self.text_on_path(el, po, pidx, holder, doc, align, size, bshift, txt)
            except Exception as ex:
                self.note("unsupported", self.name, f"text on a path: drawn straight ({type(ex).__name__}: {str(ex)[:80]})")
        holder.add(el)

    def text_on_path(self, el, po, pidx, holder, doc, align, size, bshift, txt):
        """AE Path Options (text on a mask) -> Rive TextFollowPathModifier on an unpainted Shape holding the mask path.
        Measured in Rive 1.2: the modifier ignores the Text's own position (glyphs go on the path, in the path's
        space), places each glyph at its layout x + offset × path length (offset is a fraction), and puts the TOP of
        the line (baseline - ascent) on the path — a translation group declared AFTER it moves the baseline back onto
        the path (declared before, it squeezes the spacing). AE: left text starts at First Margin, right text ends at
        Last Margin from the end, centred text is centred on the path's middle moved by First − Last Margin (measured
        in AE 26.5: the whole margin, not half). Rive wraps
        the glyphs that pass the path's end back to its start; AE does not."""
        from .shapes import PathSource
        ms = list(self.L.masks.properties) if self.L.masks is not None else []
        if pidx > len(ms):
            raise ValueError(f"mask {pidx} not found")
        m = ms[pidx - 1]

        def opt(mn, d=0.0):
            try:
                p = po.property(mn)
                return self.prop(p, clean(p.name)) if p is not None else None
            except Exception:
                return None
        rev = opt("ADBE Text Reverse Path")
        reverse = bool(rev is not None and tonum(rev.static(None, 0)))
        ap = self.prop(m.property("ADBE Mask Shape"), clean(m.name) + " path")
        ps = PathSource.from_prop(ap, 1)
        if ps is not None and reverse:
            # the text runs from the other END of an open path (PathSource's reverse keeps the first vertex first)
            if ps.static is not None:
                ps.static = geom.reverse_open(ps.static)
            if ps.keys is not None:
                ps.keys = [(t, geom.reverse_open(q)) for t, q in ps.keys]
            if ps.frames is not None:
                ps.frames = [geom.reverse_open(q) for q in ps.frames]
        pp = self.points_path("tpath", clean(m.name), ps) if ps is not None else None
        if pp is None:
            raise ValueError("empty mask path")
        sh = E("Shape", id=self.id("tpath", "sh"), name=f"{self.name} · text path ({clean(m.name)})")
        sh.add(pp)
        holder.add(sh)
        samples = ps.samples()
        lens = [geom.path_length(s) for s in samples]
        if ap.animated and max(lens) - min(lens) > 1e-3:
            self.note("approx", self.name, "text on an animated path: the offset uses the path's first length")
        L0 = max(lens[0], 1e-6)
        fm, lm = opt("ADBE Text First Margin"), opt("ADBE Text Last Margin")

        # Rive lays the glyphs from distance offset × length (a centred or right-aligned auto-width line still starts
        # at 0, and originX moves the whole text on screen, off the path): the text starts where AE puts its first
        # glyph — the alignment is computed here from the line's width
        el.set("originX", 0.0).set("alignValue", "left")
        width = self.conv.font_asset(self._font_ps(doc))[2][2](txt.split("\n")[0], size)
        tracking = float(getattr(doc, "tracking", 0) or 0)
        width += tracking * size / 1000.0 * max(0, len(txt.split("\n")[0]) - 1)

        def dist(t):
            a = tonum(unwrap(fm.at(t))) if fm is not None else 0.0
            b = tonum(unwrap(lm.at(t))) if lm is not None else 0.0
            if align == "center":
                return L0 / 2.0 + a - b - width / 2.0
            if align == "right":
                return L0 - b - width
            return a
        g = E("TextModifierGroup", id=self.id("tpath", "g"), name="Path Options")
        g.add(E("TextModifierRange", id=self.id("tpath", "r"), name="All", modifyFrom=-1.0, modifyTo=2.0,
                falloffFrom=-1.0, falloffTo=2.0))
        fp = g.add(E("TextFollowPathModifier", id=self.id("tpath", "fp"), name="Follow path", targetId=sh.id,
                     offset=dist(0.0) / L0))
        perp = opt("ADBE Text Perpendicular To Path")
        if perp is not None and not tonum(perp.static(None, 1)):
            fp.set("orient", False)
        if any(p is not None and p.animated for p in (fm, lm)):
            self.put_dense(fp, "offset", [(f, dist(f / self.tl.fps) / L0) for f in range(self.tl.nframes + 1)])
        el.add(g)
        asc = self.conv.font_asset(self._font_ps(doc))[2][0]
        base = E("TextModifierGroup", id=self.id("tpath", "base"), name="Baseline on path", modifyTranslation=True,
                 y=asc * size - bshift)
        base.add(E("TextModifierRange", id=self.id("tpath", "base", "r"), name="All", modifyFrom=-1.0, modifyTo=2.0,
                   falloffFrom=-1.0, falloffTo=2.0))
        el.add(base)
        fa = opt("ADBE Text Force Align Path")
        if fa is not None and tonum(fa.static(None, 0)):
            self.note("approx", self.name, "text on a path: Force Alignment (spread between the margins) not reproduced")
        self.note("converted", self.name, f"text on a path (mask '{clean(m.name)}') → TextFollowPathModifier")

    @staticmethod
    def _font_ps(src):
        fo = getattr(src, "font_object", None)
        return clean(getattr(fo, "post_script_name", None) or getattr(src, "font", "") or "Montserrat-Bold")

    def text_style(self, src, key, lead):
        """TextStylePaint for one AE character style: the document's (key ()) or a CharacterRange's"""
        conv = self.conv
        fo = getattr(src, "font_object", None)
        ps = clean(getattr(fo, "post_script_name", None) or getattr(src, "font", "") or "Montserrat-Bold")
        aid, rel, (asc, desc, measure), fam, sty = conv.font_asset(ps)
        size = float(getattr(src, "font_size", 0) or 12)
        style = E("TextStylePaint", id=self.id("style", *key), name="Style" + (f" {key[-1] + 1}" if key else ""),
                  fontSize=size, fontAssetId=aid)
        if fam:
            style.set("familyName", fam)
        if sty:
            style.set("styleName", sty)
        tracking = float(getattr(src, "tracking", 0) or 0)
        if tracking:
            style.set("letterSpacing", tracking * size / 1000.0)
        style.set("lineHeight", lead)
        paints = []
        # py-aep leaves apply_fill at None when the document does not store it (7 of 8 texts of Infinity's Web
        # Elements 01, all drawn by AE): AE then draws the fill colour it has
        apply_fill = getattr(src, "apply_fill", None)
        if apply_fill is None:
            apply_fill = getattr(src, "fill_color", None) is not None
        apply_stroke = bool(getattr(src, "apply_stroke", False))
        if apply_fill and getattr(src, "fill_color", None) is not None:
            f = E("Fill", id=self.id("tfill", *key), name="Fill")
            sc = f.add(E("SolidColor", id=self.id("tfill", *key, "c"), name="Color", colorValue=argb(list(src.fill_color), 1.0)))
            self.colors.append(("solid", sc))
            paints.append(("fill", f))
        if apply_stroke and float(getattr(src, "stroke_width", 0) or 0) > 0 and getattr(src, "stroke_color", None) is not None:
            s = E("Stroke", id=self.id("tstroke", *key), name="Stroke", thickness=float(src.stroke_width))
            sc = s.add(E("SolidColor", id=self.id("tstroke", *key, "c"), name="Color", colorValue=argb(list(src.stroke_color), 1.0)))
            self.colors.append(("solid", sc))
            paints.append(("stroke", s))
        # Rive: the last paint draws on top
        if len(paints) == 2 and not getattr(src, "stroke_over_fill", True):
            paints.reverse()
        for _, p in paints:
            style.add(p)
        return style

    def style_runs(self, doc):
        """[(text, CharacterRange, style key)] of the document's character-style runs (one entry: a uniform text)"""
        raw = getattr(doc, "text", "") or ""
        if len(raw) < 2 or not hasattr(doc, "character_range"):
            return []

        def key_of(r):
            fo = getattr(r, "font_object", None)
            col = lambda c: tuple(round(float(x), 4) for x in c) if c is not None else None
            return (clean(getattr(fo, "post_script_name", None) or getattr(r, "font", "") or ""),
                    round(float(getattr(r, "font_size", 0) or 0), 3), col(getattr(r, "fill_color", None)),
                    getattr(r, "apply_fill", None), col(getattr(r, "stroke_color", None)),
                    round(float(getattr(r, "stroke_width", 0) or 0), 3), bool(getattr(r, "apply_stroke", False)),
                    round(float(getattr(r, "tracking", 0) or 0), 3))
        try:
            ranges = [doc.character_range(i, i + 1) for i in range(len(raw))]
            keys = [key_of(r) for r in ranges]
        except Exception:
            return []
        runs, start = [], 0
        for i in range(1, len(raw) + 1):
            if i == len(raw) or keys[i] != keys[start]:
                seg = clean(raw[start:i]).replace("\r\n", "\n").replace("\r", "\n").replace("\x03", "\n")
                if getattr(doc, "all_caps", False):
                    seg = seg.upper()
                runs.append((seg, ranges[start], keys[start]))
                start = i
        return runs

    @staticmethod
    def _neutral_text_prop(mn, v):
        """an animator property at the value that changes nothing"""
        if mn == "ADBE Text Line Anchor":
            return True                               # only matters with a tracking amount
        neutral = {"ADBE Text Skew": 0, "ADBE Text Skew Axis": 0, "ADBE Text Stroke Width": 0,
                   "ADBE Text Tracking Amount": 0, "ADBE Text Character Replace": 0, "ADBE Text Character Offset": 0,
                   "ADBE Text Line Spacing": 0, "ADBE Text Blur": 0, "ADBE Text Position 3D": 0,
                   "ADBE Text Rotation": 0, "ADBE Text Rotation X": 0, "ADBE Text Rotation Y": 0}
        if mn not in neutral:
            return False
        vals = v if isinstance(v, list) else [v]
        try:
            return all(abs(tonum(x) - neutral[mn]) < 1e-6 for x in vals)
        except Exception:
            return False

    def text_animators(self, el, anims, txt, doc=None):
        """AE text animators -> Rive TextModifierGroup + TextModifierRange (range selectors).
        AE's square shape covers a unit partially while the range edge crosses it: reproduced with falloff ramps one
        (smoothness-scaled) unit wide around each edge — Rive samples coverage at each glyph's centre."""
        from .shapes import _stored
        try:
            items = [a for a in anims.properties if getattr(a, "enabled", True)]
        except Exception:
            items = []
        units_of = {1: "characters", 2: "charactersExcludingSpaces", 3: "words", 4: "lines"}
        mode_of = {1: "add", 2: "subtract", 3: "multiply", 4: "min", 5: "max", 6: "difference"}
        count = {"characters": max(1, len(txt.replace("\n", ""))),
                 "charactersExcludingSpaces": max(1, len("".join(txt.split()))),
                 "words": max(1, len(txt.split())), "lines": max(1, len(txt.split("\n")))}
        try:
            # lines AE composed (a paragraph wraps in its box): a per-line animator counts these, not the returns
            # (Mh_LST_Resp_Drinking: only the first wrapped line moved, over the second)
            n = int(getattr(doc, "composed_line_count", 0) or 0)
            if n > 0:
                count["lines"] = n
        except Exception:
            pass
        glyph_jobs = []
        for ai, a in enumerate(items):
            aname = clean(a.name)
            pg = a.property("ADBE Text Animator Properties")
            sg = a.property("ADBE Text Selectors")
            props = {getattr(p, "match_name", ""): p for p in (pg.properties if pg is not None else []) if _stored(p)}
            g = E("TextModifierGroup", id=self.id("anim", ai), name=aname)
            done, skipped = [], []
            tracking = None
            for mn, p in props.items():
                ap = self.prop(p, f"{aname} {clean(p.name)}")
                if mn == "ADBE Text Position 3D":
                    g.set("modifyTranslation", True)
                    self.put(g, "x", ap, 0, force=True)
                    self.put(g, "y", ap, 1, force=True)
                    done.append("position")
                elif mn == "ADBE Text Scale 3D":
                    g.set("modifyScale", True)
                    self.put(g, "scaleX", ap, 0, 0.01, force=True)
                    self.put(g, "scaleY", ap, 1, 0.01, force=True)
                    done.append("scale")
                elif mn == "ADBE Text Rotation":
                    g.set("modifyRotation", True)
                    self.put(g, "rotation", ap, None, math.pi / 180, force=True)
                    done.append("rotation")
                elif mn == "ADBE Text Opacity":
                    g.set("modifyOpacity", True).set("invertOpacity", True)
                    self.put(g, "opacity", ap, None, 0.01, force=True)
                    done.append("opacity")
                elif mn in ("ADBE Text Fill Color", "ADBE Text Stroke Color", "ADBE Text Character Offset"):
                    if mn == "ADBE Text Character Offset" and not ap.animated and abs(tonum(ap.static(None, 0))) < 1e-6:
                        continue
                    glyph_jobs.append((mn, ap, sg, aname))
                    continue
                elif mn == "ADBE Text Tracking Amount":
                    if ap.animated or abs(tonum(ap.static(None, 0))) > 1e-6:
                        tracking = ap
                    continue
                elif mn in ("ADBE Text Line Anchor", "ADBE Text Track Type"):
                    continue                          # read with the tracking amount
                elif mn in ("ADBE Text Anchor Point 3D",) and not ap.animated and all(abs(tonum(x)) < 1e-6 for x in (ap.static(None) or [0])):
                    continue
                elif not ap.animated and self._neutral_text_prop(mn, ap.static(None)):
                    continue                          # stored but left at its no-effect value (TextEvo presets)
                else:
                    skipped.append(clean(p.name))
            if skipped:
                self.note("unsupported", f"{self.name} · {aname}", f"text animator properties not in Rive: {', '.join(skipped)}")
            bake = self._needs_bake(sg)
            if tracking is not None or (done and bake):
                strengths = self._unit_strengths(sg, txt, count)
                if strengths is not None:
                    if done:
                        n = len(strengths[0])
                        for k in range(n):
                            r = E("TextModifierRange", id=self.id("anim", ai, "u", k), name=f"{aname} {k + 1}",
                                  unitsValue="characters", typeValue="unitIndex", modeValue="add",
                                  modifyFrom=float(k), falloffFrom=float(k), falloffTo=float(k + 1), modifyTo=float(k + 1))
                            self.put_dense(r, "strength", [(f, st[k]) for f, st in enumerate(strengths)])
                            g.add(r)
                        el.add(g)
                        self.note("converted", f"{self.name} · {aname}", f"text animator → modifier ({', '.join(done)}), "
                                  f"selector strengths baked per character ({bake})")
                        if "randomize" in bake or "wiggly" in bake:
                            self.note("approx", f"{self.name} · {aname}", "AE's random order / wiggle noise is not public: "
                                      "same kind of motion, not the same characters")
                    if tracking is not None:
                        self._tracking_groups(el, a, ai, aname, tracking, strengths, txt)
                    continue
            if not done:
                continue
            nr = 0
            for si, s in enumerate(sg.properties if sg is not None else []):
                if not getattr(s, "enabled", True):
                    continue
                smn = getattr(s, "match_name", "")
                if smn == "ADBE Text Expressible Selector":
                    rs = self.text_expr_selector(s, ai, si, units_of, mode_of, count)
                    for r in rs:
                        g.add(r)
                    nr += len(rs)
                    continue
                if smn != "ADBE Text Selector":
                    self.note("unsupported", f"{self.name} · {aname}", f"{smn.replace('ADBE Text ', '')} not converted "
                                                                       "(range selectors only)")
                    continue
                r = self.text_range(s, ai, si, units_of, mode_of, count)
                if r is not None:
                    g.add(r)
                    nr += 1
            if nr == 0:
                self.note("approx", f"{self.name} · {aname}", "animator without a range selector: applied to the whole text")
                g.add(E("TextModifierRange", id=self.id("anim", ai, "all"), name="All", modifyFrom=-1.0, modifyTo=2.0,
                        falloffFrom=-1.0, falloffTo=2.0))
            el.add(g)
            self.note("converted", f"{self.name} · {aname}", f"text animator → modifier ({', '.join(done)}; {nr} range(s))")
        if glyph_jobs:
            self._per_char_runs(el, txt, count, glyph_jobs)

    def _needs_bake(self, sg):
        """why a selector set cannot map to Rive ranges directly ('' when it can)"""
        why = []
        for s in (sg.properties if sg is not None else []):
            if not getattr(s, "enabled", True):
                continue
            smn = getattr(s, "match_name", "")
            if smn == "ADBE Text Wiggly Selector":
                why.append("wiggly selector")
            elif smn == "ADBE Text Selector":
                adv = s.property("ADBE Text Range Advanced")

                def v(mn, d=0.0):
                    try:
                        return tonum(adv.property(mn).value)
                    except Exception:
                        return d
                if v("ADBE Text Randomize Order"):
                    why.append("randomize order")
                if abs(v("ADBE Text Levels Max Ease")) > 1e-6 or abs(v("ADBE Text Levels Min Ease")) > 1e-6:
                    why.append("ease high/low")
        return ", ".join(dict.fromkeys(why))

    def _unit_strengths(self, sg, txt, count):
        """[frame][character] combined strength (0..1, × amount) of an animator's selectors, AE's way (textsel)"""
        from . import textsel
        chars = [c for c in txt if c != "\n"]
        n = len(chars)
        if n == 0 or n * (self.tl.nframes + 1) > 400000:
            return None
        # unit index of each character, per "based on"
        word_of, w, prev_space = [], -1, True
        for c in chars:
            if not c.isspace() and prev_space:
                w += 1
            word_of.append(max(w, 0) if not c.isspace() else -1)
            prev_space = c.isspace()
        line_of, li = [], 0
        for c in txt:
            if c == "\n":
                li += 1
            else:
                line_of.append(li)
        nospace_of, k = [], -1
        for c in chars:
            if c.isspace():
                nospace_of.append(-1)
            else:
                k += 1
                nospace_of.append(k)
        unit_maps = {1: (list(range(n)), n), 2: (nospace_of, max(1, k + 1)), 3: (word_of, max(1, w + 1)),
                     4: (line_of, max(1, li + 1))}
        mode_of = {1: "add", 2: "subtract", 3: "multiply", 4: "min", 5: "max", 6: "difference"}
        sels = [s for s in (sg.properties if sg is not None else []) if getattr(s, "enabled", True)]

        def pv(prop, t, d):
            if prop is None:
                return d
            try:
                ap = self.prop(prop, clean(prop.name))
                return tonum(unwrap(ap.at(t))) if ap.animated else tonum(ap.static(None, d))
            except Exception:
                return d
        out = []
        for f in range(self.tl.nframes + 1):
            t = f / self.tl.fps
            acc = [0.0] * n
            for si, s in enumerate(sels):
                smn = getattr(s, "match_name", "")
                if smn == "ADBE Text Selector":
                    adv = s.property("ADBE Text Range Advanced")
                    a = lambda mn, d: pv(adv.property(mn) if adv is not None else None, t, d)
                    based = int(a("ADBE Text Range Type2", 1))
                    umap, nu = unit_maps.get(based, unit_maps[1])
                    index = int(a("ADBE Text Range Units", 1)) == 2
                    div = 1.0 if index else 100.0 / nu
                    pre = "ADBE Text Index " if index else "ADBE Text Percent "
                    o = pv(s.property(pre + "Offset"), t, 0.0) / div
                    st = pv(s.property(pre + "Start"), t, 0.0) / div + o
                    en = pv(s.property(pre + "End"), t, 0.0 if index else 100.0) / div + o
                    shape = int(a("ADBE Text Range Shape", 1))
                    smooth = a("ADBE Text Selector Smoothness", 100.0)
                    eh, el_ = a("ADBE Text Levels Max Ease", 0.0), a("ADBE Text Levels Min Ease", 0.0)
                    amount = a("ADBE Text Selector Max Amount", 100.0) / 100.0
                    mode = mode_of.get(int(a("ADBE Text Selector Mode", 1)), "add")
                    order = textsel.random_order(nu, a("ADBE Text Random Seed", 0)) if a("ADBE Text Randomize Order", 0) else None
                    vals = []
                    for c in range(n):
                        u = umap[c]
                        if u < 0:
                            vals.append(0.0)
                            continue
                        ui = order[u] if order else u
                        vals.append(textsel.range_mult(ui, st, en, shape, smooth, eh, el_) * amount)
                elif smn == "ADBE Text Wiggly Selector":
                    w_ = lambda mn, d: pv(s.property(mn), t, d)
                    based = int(w_("ADBE Text Range Type2", 1))
                    umap, nu = unit_maps.get(based, unit_maps[1])
                    hi, lo = w_("ADBE Text Wiggly Max Amount", 100.0) / 100.0, w_("ADBE Text Wiggly Min Amount", -100.0) / 100.0
                    freq, corr = w_("ADBE Text Temporal Freq", 2.0), w_("ADBE Text Character Correlation", 50.0) / 100.0
                    tph, sph = w_("ADBE Text Temporal Phase", 0.0), w_("ADBE Text Spatial Phase", 0.0)
                    seed = int(w_("ADBE Text Wiggly Random Seed", 0))
                    mode = mode_of.get(int(w_("ADBE Text Selector Mode", 3)), "multiply")
                    x = t * freq + tph / 360.0
                    shared = textsel.smooth_noise(seed, x)
                    vals = []
                    for c in range(n):
                        u = umap[c]
                        if u < 0:
                            vals.append(0.0)
                            continue
                        own = textsel.smooth_noise(seed + 17 * (u + 1) + int(sph), x)
                        nz = shared * corr + own * (1 - corr)
                        vals.append(lo + (hi - lo) * (nz + 1) / 2)
                    if si == 0 and mode == "multiply":
                        acc = [1.0] * n                # a lone wiggly selector wiggles the whole text
                else:
                    continue
                acc = [textsel.combine(mode, acc[c], vals[c]) for c in range(n)]
            out.append([max(-1.0, min(1.0, v)) for v in acc])
        if not sels:
            out = [[1.0] * n for _ in out]
        return out

    def _per_char_runs(self, el, txt, count, jobs):
        """Fill / Stroke Color and Character Offset animators: Rive's modifiers cannot recolour or change glyphs, so the
        text becomes one TextValueRun per character, each with its own style; the colour (AE: the animator colour
        mixed in by the selector strength, animators in order) and the character are keyed per frame."""
        styles = [c for c in el.children if c.tag == "TextStylePaint"]
        runs = [c for c in el.children if c.tag == "TextValueRun"]
        if len(runs) != 1 or len(styles) != 1 or getattr(self, "_text_keyed", False) or len(txt) > 200:
            self.note("unsupported", self.name, "colour / character offset animators on a text with several styles, "
                                                "keyed source text or over 200 characters: not converted")
            return
        style, run = styles[0], runs[0]
        el.children.remove(run)
        n_chars = len([c for c in txt if c != "\n"])
        strengths = {}
        for mn, ap, sg, aname in jobs:
            if id(sg) not in strengths:
                strengths[id(sg)] = self._unit_strengths(sg, txt, count) or [[1.0] * n_chars] * (self.tl.nframes + 1)
        nf = self.tl.nframes + 1

        def paint_color(kind):
            for pt in style.children:
                if pt.tag == kind:
                    for sc in pt.children:
                        if sc.tag == "SolidColor":
                            return sc.attrs.get("colorValue")
            return None
        idx = 0
        made = 0
        offset_chars = {}
        for ch in txt:
            if ch == "\n":
                el.add(E("TextValueRun", id=self.id("crun", "nl", made), name="Line break", styleId=style.id, text="\n"))
                made += 1
                continue
            k = idx
            idx += 1
            st = self.clone(style, "TextStylePaint")
            st.name = f"Style {k + 1}"
            el.insert(el.children.index(style) + 1 + k, st)
            r = el.add(E("TextValueRun", id=self.id("crun", k), name=f"Char {k + 1}", styleId=st.id, text=ch))
            made += 1
            for kind, mnc in (("Fill", "ADBE Text Fill Color"), ("Stroke", "ADBE Text Stroke Color")):
                base = paint_color(kind)
                cj = [(ap, sg) for mn, ap, sg, _ in jobs if mn == mnc]
                if base is None or not cj:
                    continue
                b = [int(base[i:i + 2], 16) / 255.0 for i in (2, 4, 6)]
                alpha = int(base[0:2], 16) / 255.0
                keys, last = [], None
                for f in range(nf):
                    c = list(b)
                    for ap, sg in cj:
                        col = ap.at(f / self.tl.fps) if ap.animated else ap.static(None)
                        col = list(to_js(col))[:3] if col is not None else c
                        w = max(0.0, min(1.0, strengths[id(sg)][f][k]))
                        c = [c[i] + (float(col[i]) - c[i]) * w for i in range(3)]
                    v = argb(c, alpha)
                    if v != last:
                        if keys and keys[-1][0] < f - 1:
                            keys.append((f - 1, last, "linear", None))
                        keys.append((f, v, "linear", None))
                        last = v
                for pt in st.children:
                    if pt.tag == kind:
                        for sc in pt.children:
                            if sc.tag == "SolidColor":
                                sc.set("colorValue", keys[0][1])
                                if len(keys) > 1:
                                    self.anim.put(sc.id, "SolidColor", "colorValue", keys, kind="color")
            oj = [(ap, sg) for mn, ap, sg, _ in jobs if mn == "ADBE Text Character Offset"]
            if oj and not ch.isspace():
                keys, last = [], None
                for f in range(nf):
                    off = 0.0
                    for ap, sg in oj:
                        v = tonum(unwrap(ap.at(f / self.tl.fps))) if ap.animated else tonum(ap.static(None, 0))
                        off += v * strengths[id(sg)][f][k]
                    c2 = self._offset_char(ch, int(math.floor(off + 0.5 + 1e-9)))
                    offset_chars.setdefault(k, {})[f] = c2
                    if c2 != last:
                        keys.append((f, c2, "hold", None))
                        last = c2
                r.set("text", keys[0][1])
                if len(keys) > 1:
                    self.anim.put(r.id, "TextValueRun", "text", keys, kind="string")
        self._offset_slots(el, txt, jobs, offset_chars)
        what = sorted({mn.replace("ADBE Text ", "") for mn, *_ in jobs})
        self.note("converted", self.name, f"{', '.join(what)} → one run per character ({idx}), keyed per frame")

    def _offset_slots(self, el, txt, jobs, offset_chars):
        """AE draws an offset character in the ORIGINAL character's slot (Character Alignment left / centre / right;
        'adjust kerning' reflows): Rive reflows the line with the new advances, so each character is moved back by a
        keyed translation — the original layout's x minus the reflowed one, both lines aligned as the text is."""
        ps, size, align = getattr(self, "_text_font", (None, 0, "left"))
        if not ps or "\n" in txt:
            return
        mode = 1
        for mn, ap, sg, aname in jobs:
            pass
        try:
            for mn, ap, sg, aname in jobs:
                if mn == "ADBE Text Character Offset":
                    mode = int(tonum(ap.p.parent_property.property("ADBE Text Character Change Type").value))
        except Exception:
            mode = 1
        if mode == 4:
            return
        frac = {1: 0.0, 2: 0.5, 3: 1.0}.get(mode, 0.0)
        just = {"center": 0.5, "right": 1.0}.get(align, 0.0)
        measure = self.conv.font_asset(ps)[2][2]
        # the original line as AE lays it: shaped, kerning included (one run per character loses the kerning
        # between neighbours in Rive: each character then needs moving back, even without an offset)
        adv0 = [measure(c, size) for c in txt]
        try:
            import uharfbuzz as hb
            path = os.path.join(self.conv.out_dir, self.conv.font_asset(ps)[1])
            hfont = hb.Font(hb.Face(hb.Blob.from_file_path(path)))
            buf = hb.Buffer()
            buf.add_str(txt)
            buf.guess_segment_properties()
            hb.shape(hfont, buf, {"kern": True})
            pos = [p.x_advance / hfont.face.upem * size for p in buf.glyph_positions]
            if len(pos) == len(txt):
                adv0 = pos
        except Exception:
            pass
        W0 = sum(adv0)
        nf = self.tl.nframes + 1
        cache = {}
        shifts = [[0.0] * len(txt) for _ in range(nf)]
        for f in range(nf):
            cur = [offset_chars.get(k, {}).get(f, c) for k, c in enumerate(txt)]
            adv1 = [cache.setdefault(c, measure(c, size)) for c in cur]
            W1 = sum(adv1)
            x0 = x1 = 0.0
            for k in range(len(txt)):
                ae = -just * W0 + x0 + (adv0[k] - adv1[k]) * frac
                rv = -just * W1 + x1
                shifts[f][k] = ae - rv
                x0 += adv0[k]
                x1 += adv1[k]
        for k in range(len(txt)):
            dense = [(f, sh[k]) for f, sh in enumerate(shifts)]
            if max(abs(v) for _, v in dense) < 1e-3:
                continue
            g = E("TextModifierGroup", id=self.id("coff", k), name=f"Character slot {k + 1}", modifyTranslation=True)
            self.put_dense(g, "x", dense, hold=True)
            g.add(E("TextModifierRange", id=self.id("coff", k, "r"), name=f"char {k + 1}", unitsValue="characters",
                    typeValue="unitIndex", modeValue="add", modifyFrom=float(k), falloffFrom=float(k),
                    falloffTo=float(k + 1), modifyTo=float(k + 1)))
            el.add(g)

    @staticmethod
    def _offset_char(ch, n):
        """AE Character Offset, 'preserve case & digits': letters cycle within their case, digits within 0-9"""
        if n == 0:
            return ch
        if "A" <= ch <= "Z":
            return chr(ord("A") + (ord(ch) - ord("A") + n) % 26)
        if "a" <= ch <= "z":
            return chr(ord("a") + (ord(ch) - ord("a") + n) % 26)
        if "0" <= ch <= "9":
            return chr(ord("0") + (ord(ch) - ord("0") + n) % 10)
        return ch

    def _tracking_groups(self, el, a, ai, aname, tracking, strengths, txt):
        """AE Tracking Amount (animator): 1 px per unit (measured), split before / after each selected character by
        the Tracking Type, the line widened around its Line Anchor (50 %: both sides). Baked as one translation group
        per character, keyed per frame."""
        pg = a.property("ADBE Text Animator Properties")

        def static(mn, d):
            try:
                return tonum(pg.property(mn).value)
            except Exception:
                return d
        anchor = static("ADBE Text Line Anchor", 50.0) / 100.0
        ttype = int(static("ADBE Text Track Type", 1))
        before, after = {1: (0.5, 0.5), 2: (1.0, 0.0), 3: (0.0, 1.0)}.get(ttype, (0.5, 0.5))
        lines = txt.split("\n")
        n = sum(len(l) for l in lines)
        shifts = [[0.0] * n for _ in strengths]
        for f, st in enumerate(strengths):
            amt = tonum(unwrap(tracking.at(f / self.tl.fps))) if tracking.animated else tonum(tracking.static(None, 0))
            base = 0
            for line in lines:
                m = len(line)
                cum, xs = 0.0, []
                for k in range(m):
                    e = amt * st[base + k]
                    cum += e * before
                    xs.append(cum)
                    cum += e * after
                for k in range(m):
                    shifts[f][base + k] = xs[k] - anchor * cum
                base += m
        for k in range(n):
            dense = [(f, sh[k]) for f, sh in enumerate(shifts)]
            if max(abs(v) for _, v in dense) < 1e-6:
                continue
            g = E("TextModifierGroup", id=self.id("anim", ai, "trk", k), name=f"{aname} tracking {k + 1}",
                  modifyTranslation=True)
            self.put_dense(g, "x", dense)
            g.add(E("TextModifierRange", id=self.id("anim", ai, "trk", k, "r"), name=f"char {k + 1}",
                    unitsValue="characters", typeValue="unitIndex", modeValue="add",
                    modifyFrom=float(k), falloffFrom=float(k), falloffTo=float(k + 1), modifyTo=float(k + 1)))
            el.add(g)
        self.note("converted", f"{self.name} · {aname}", f"tracking amount → per-character translation ({n} characters, "
                                                          "baked per frame)")

    def text_expr_selector(self, s, ai, si, units_of, mode_of, count):
        """AE Expression Selector -> one TextModifierRange per unit (by unit index), its strength keyed per frame from
        the Amount expression evaluated with that unit's textIndex / textTotal: every unit gets AE's amount at every
        frame. First component of the amount (AE applies it per dimension)."""
        eng, tl = self.tl.engine, self.tl

        def sub(mn):
            try:
                return s.property(mn)
            except Exception:
                return None
        amt = sub("ADBE Text Expressible Amount")
        based, modep = sub("ADBE Text Range Type2"), sub("ADBE Text Selector Mode")
        units = units_of.get(int(tonum(based.value)) if based is not None else 1, "characters")
        mode = mode_of.get(int(tonum(modep.value)) if modep is not None else 1, "add")
        n = int(count.get(units, 1))
        where = f"{self.name} · {clean(s.name)}"
        if amt is None:
            return []
        if not eng.has_expr(amt) or n * (tl.nframes + 1) > 200000:
            if eng.has_expr(amt):
                self.note("approx", where, f"expression selector on {n} units × {tl.nframes + 1} frames: too many to "
                                           "evaluate one by one, its static amount used")
            v = unwrap(to_js(amt.value))
            r = E("TextModifierRange", id=self.id("anim", ai, "r", si), name=clean(s.name), unitsValue=units,
                  modeValue=mode, modifyFrom=-1.0, modifyTo=2.0, falloffFrom=-1.0, falloffTo=2.0,
                  strength=tonum(v[0] if isinstance(v, list) else v) / 100.0)
            return [r]
        sel = [100.0, 100.0, 100.0]
        out = []
        try:
            for i in range(n):
                dense = []
                for f in range(tl.nframes + 1):
                    v, _dep = eng.eval_extra(amt, self.L, tl.comp, f / tl.fps,
                                             {"textIndex": float(i + 1), "textTotal": float(n), "selectorValue": sel})
                    v = unwrap(v)
                    dense.append((f, tonum(v[0] if isinstance(v, list) else v) / 100.0))
                r = E("TextModifierRange", id=self.id("anim", ai, "r", si, i), name=f"{clean(s.name)} {i + 1}",
                      unitsValue=units, typeValue="unitIndex", modeValue=mode, modifyFrom=float(i),
                      falloffFrom=float(i), falloffTo=float(i + 1), modifyTo=float(i + 1))
                self.put_dense(r, "strength", dense)
                out.append(r)
        except Exception as ex:
            self.note("unsupported", where, f"expression selector not evaluated ({type(ex).__name__}: {str(ex)[:80]})")
            return []
        self.note("converted", where, f"expression selector → {n} per-{units} ranges, strength keyed from the expression")
        return out

    def text_range(self, s, ai, si, units_of, mode_of, count):
        adv = s.property("ADBE Text Range Advanced")

        def adv_v(mn, d):
            p = adv.property(mn) if adv is not None else None
            try:
                return tonum(p.value) if p is not None else d
            except Exception:
                return d
        units = units_of.get(int(adv_v("ADBE Text Range Type2", 1)), "characters")
        index = int(adv_v("ADBE Text Range Units", 1)) == 2
        shape = int(adv_v("ADBE Text Range Shape", 1))
        smooth = adv_v("ADBE Text Selector Smoothness", 100.0) / 100.0
        mode = mode_of.get(int(adv_v("ADBE Text Selector Mode", 1)), "add")
        if adv_v("ADBE Text Randomize Order", 0):
            self.note("unsupported", self.name, "range selector 'randomize order' is not in Rive: in order")
        if abs(adv_v("ADBE Text Levels Max Ease", 0)) > 1e-6 or abs(adv_v("ADBE Text Levels Min Ease", 0)) > 1e-6:
            self.note("approx", self.name, "range selector ease high/low not reproduced")
        k = 1.0 if index else 0.01
        n = 1.0 if index else float(count.get(units, 1))
        w = (smooth if shape == 1 else 0.0) / (1.0 if index else n)
        sp = self.prop(s.property("ADBE Text Index Start" if index else "ADBE Text Percent Start"), "range start")
        ep = self.prop(s.property("ADBE Text Index End" if index else "ADBE Text Percent End"), "range end")
        op = self.prop(s.property("ADBE Text Index Offset" if index else "ADBE Text Percent Offset"), "range offset")
        amt = adv.property("ADBE Text Selector Max Amount") if adv is not None else None
        r = E("TextModifierRange", id=self.id("anim", ai, "r", si), name=clean(s.name), unitsValue=units,
              typeValue="unitIndex" if index else "percentage", modeValue=mode)
        if amt is not None:
            self.put(r, "strength", self.prop(amt, "range amount"), None, 0.01, default=1.0)
        self.put(r, "offset", op, None, k)
        far = 1000.0
        if shape == 1:
            self.put(r, "modifyFrom", sp, None, k, -w / 2, force=True)
            self.put(r, "falloffFrom", sp, None, k, w / 2, force=True)
            self.put(r, "falloffTo", ep, None, k, -w / 2, force=True)
            self.put(r, "modifyTo", ep, None, k, w / 2, force=True)
        elif shape == 2:                                   # ramp up
            self.put(r, "modifyFrom", sp, None, k, force=True)
            self.put(r, "falloffFrom", ep, None, k, force=True)
            r.set("falloffTo", far).set("modifyTo", far)
        elif shape == 3:                                   # ramp down
            r.set("modifyFrom", -far).set("falloffFrom", -far)
            self.put(r, "falloffTo", sp, None, k, force=True)
            self.put(r, "modifyTo", ep, None, k, force=True)
        else:                                              # triangle / round / smooth: peak in the middle
            self.put(r, "modifyFrom", sp, None, k, force=True)
            self.put(r, "modifyTo", ep, None, k, force=True)
            if sp.animated and ep.animated:
                dense = [(f, (tonum(unwrap(sp.at(f / self.tl.fps))) + tonum(unwrap(ep.at(f / self.tl.fps)))) / 2 * k)
                         for f in range(self.tl.nframes + 1)]
                self.put_dense(r, "falloffFrom", dense)
                self.put_dense(r, "falloffTo", dense)
            elif ep.animated:
                mid_b = tonum(sp.static(None)) / 2 * k
                self.put(r, "falloffFrom", ep, None, k / 2, mid_b, force=True)
                self.put(r, "falloffTo", ep, None, k / 2, mid_b, force=True)
            else:
                mid_b = tonum(ep.static(None)) / 2 * k
                self.put(r, "falloffFrom", sp, None, k / 2, mid_b, force=True)
                self.put(r, "falloffTo", sp, None, k / 2, mid_b, force=True)
            if shape in (5, 6):
                r.add(E("CubicInterpolatorComponent", id=self.id("anim", ai, "r", si, "ease"), name="Ease",
                        x1=0.42, y1=0.0, x2=0.58, y2=1.0))
        return r

    @staticmethod
    def _doc_text(v):
        t = clean(getattr(v, "text", v))
        return t.replace("\r\n", "\n").replace("\r", "\n")

    # ---------------------------------------------------------------- masks
    def mask_items(self):
        L = self.L
        try:
            ms = list(L.masks.properties) if L.masks is not None else []
        except Exception:
            ms = []
        out = []
        for m in ms:
            try:
                mode = int(m.mask_mode)
            except Exception:
                mode = 6813
            if mode == 6812:
                continue
            try:
                sp = m.property("ADBE Mask Shape")
                ks = list(sp.keyframes or [])
                sv = ks[0].value if ks else sp.value
                if not getattr(sv, "closed", True):
                    # an OPEN mask makes no transparency in AE (it only feeds effects such as Stroke): broadcast-test's
                    # guitar carried an 8-point open curve and AE shows the whole guitar
                    self.note("converted", clean(m.name), "open mask: no effect on the layer's alpha, as in AE")
                    continue
            except Exception:
                pass
            out.append(m)
        return out

    def masks(self, ms, content, holder):
        src_node = E("Node", id=self.id("masks"), name=self.name + " · masks")
        holder.add(src_node)
        adds, subs, inters = [], [], []
        for i, m in enumerate(ms):
            mode = int(getattr(m, "mask_mode", 6813) or 6813)
            inv = bool(getattr(m, "inverted", False))
            # modes: 6813 add, 6814 subtract, 6815 intersect, 6816 lighten, 6817 darken, 6818 difference. On opaque
            # masks lighten (max) = add and darken (min) = intersect; difference = exclusive or, drawn by the even-odd
            # rule over the add group (FX packs animate frame by frame with difference masks: broadcast-test's Handy_16 has
            # 133 on one layer, and two of them lit together cut a hole)
            if mode in (6815, 6817):
                inters.append((i, m, inv))
            elif mode == 6814 or inv:
                subs.append((i, m, inv))
            else:
                adds.append((i, m, inv))
            for mn, label, neutral in (("ADBE Mask Feather", "feather", 0.0), ("ADBE Mask Offset", "expansion", 0.0)):
                p = m.property(mn)
                try:
                    v = p.value
                    vv = max(abs(x) for x in v) if isinstance(v, list) else abs(v - neutral)
                    if vv > 1e-3:
                        self.note("approx", clean(m.name), f"mask {label} is not in Rive: hard edge")
                except Exception:
                    pass

        def mask_on(m):
            """per frame, is the mask there? A mask at 0 % opacity adds nothing. FX Monster animates frame by frame
            this way: one mask per drawing, its opacity keyed 0 / 100 (Cartoon_01: 13 drawings on one layer)"""
            op = self.prop(m.property("ADBE Mask Opacity"), clean(m.name) + " opacity")
            if not op.animated:
                o = tonum(op.static(None, 100.0))
                if 1e-3 < o < 100 - 1e-3:
                    self.note("approx", clean(m.name), f"mask opacity {o:g} % is not in Rive: {'on' if o >= 50 else 'off'}")
                return None if o >= 50 else False
            flags = [tonum(unwrap(op.at(f / self.tl.fps))) >= 50 for f in range(self.tl.nframes + 1)]
            if all(flags):
                return None
            if not any(flags):
                return False
            self.note("converted", clean(m.name), "keyed mask opacity → the mask path collapses where it is under 50 %")
            return flags

        def shape_of(items, key, with_big):
            sh = E("Shape", id=self.id("mask", key), name=self.name + " · " + ", ".join(clean(m.name) for _, m, _ in items))
            if with_big:
                sh.add(E("Rectangle", id=self.id("mask", key, "big"), name="Everything", width=BIG, height=BIG))
            for i, m, _inv in items:
                on = mask_on(m)
                if on is False:
                    continue
                ap = self.prop(m.property("ADBE Mask Shape"), clean(m.name) + " path")
                ps = PathSource.from_prop(ap, 1)
                if ps is None:
                    continue
                if on is not None and ap.animated:
                    # a drawn mask (FX Monster: one drawing per frame on the path) is read frame by frame — its keys
                    # alone showed another drawing (Cartoon_01, measured) — where it is on; off frames repeat the
                    # previous drawing (the path is scaled to 0 there): no vertex moves while it is hidden
                    from .shapes import _sv_path, _to_sv
                    frames, last = [], None
                    for f in range(self.tl.nframes + 1):
                        if on[f] or last is None:
                            last = _sv_path(_to_sv(unwrap(ap.at(f / self.tl.fps))))
                        frames.append(last)
                    ps = PathSource()
                    ps.frames = frames
                gs = getattr(self, "_plane_g", None)
                if gs and any(g is not None for g in gs):
                    # 3D layer / Corner Pin: the mask lives on the plane, it goes through the same map, per frame
                    from .shapes import _sv_path, _to_sv
                    frames = []
                    for f in range(self.tl.nframes + 1):
                        pth = _sv_path(_to_sv(unwrap(ap.at(f / self.tl.fps))))
                        g = gs[f] or next(x for x in gs if x is not None)
                        frames.append(three.h_path(pth, g))
                    ps = PathSource()
                    ps.frames = frames
                el = self.points_path(f"m{i}", clean(m.name), ps)
                if el is not None:
                    if on is not None:
                        # off frames: the path scaled to 0 (a Path is a Node), hold keys at each switch. Collapsing
                        # every vertex frame by frame instead wrote 12 million keys for broadcast-test's 1 422 drawn masks
                        keys, last = [], None
                        for f, v in enumerate(on):
                            if v != last:
                                keys.append((f, 1.0 if v else 0.0, "hold", None))
                                last = v
                        for a in ("scaleX", "scaleY"):
                            el.set(a, keys[0][1])
                            if len(keys) > 1:
                                self.anim.put(el.id, el.tag, a, keys)
                    sh.add(el)
            src_node.add(sh)
            return sh.id
        if adds:
            modes = [int(getattr(m, "mask_mode", 6813) or 6813) for _, m, _ in adds]
            xor = any(md == 6818 for md in modes)
            if xor and any(md != 6818 for md in modes[1:]):
                self.note("approx", self.name, "difference masks mixed with add / lighten masks: all combined as an "
                                               "exclusive or")
            content.add(E("ClippingShape", id=self.id("clip", "add"), name="Masks (difference)" if xor else "Masks (add)",
                          sourceId=shape_of(adds, "add", False), fillRule="evenOdd" if xor else None))
        if subs:
            content.add(E("ClippingShape", id=self.id("clip", "sub"), name="Masks (subtract)",
                          sourceId=shape_of(subs, "sub", True), fillRule="evenOdd"))
            if len(subs) > 1:
                self.note("approx", self.name, "several subtracted/inverted masks: overlaps between them are not subtracted twice")
        for i, m, inv in inters:
            content.add(E("ClippingShape", id=self.id("clip", "int", i), name=clean(m.name),
                          sourceId=shape_of([(i, m, inv)], f"int{i}", inv), fillRule="evenOdd" if inv else None))
        self.note("converted", self.name, f"{len(ms)} mask(s) → clipping")

    # ---------------------------------------------------------------- track mattes
    def matte_clip(self, M):
        """(clip source id, fill rule) for this layer's track matte (the source is built with the matte layer)"""
        L, cb = self.L, self.cb
        t = int(L.track_matte_type)
        inverted = t in (5014, 5016)
        if t in (5015, 5016):
            self.note("approx", self.name, "luma matte treated as an alpha matte (geometry of the matte layer)")
        kind = type(M).__name__
        src = getattr(M, "source", None)
        if kind == "TextLayer" or (kind == "AVLayer" and src is not None and not getattr(getattr(src, "main_source", None), "color", None)
                                    and not hasattr(src, "layers")):
            self.note("unsupported", self.name, f"track matte from a {kind if kind != 'AVLayer' else 'footage'} layer "
                                                f"('{clean(M.name)}'): not converted, the layer is drawn unmatted")
            return None
        ids = cb.matte_src.get(M.index)
        if ids is None:
            ids = (self.conv.ids.key("comp", cb.kid, "L", M.id, "mattesrc"),
                   self.conv.ids.key("comp", cb.kid, "L", M.id, "mattesrcinv"))
            cb.matte_src[M.index] = ids
        self.note("converted", self.name, f"track matte '{clean(M.name)}'{' (inverted)' if inverted else ''} → clipping")
        return (ids[1], "evenOdd") if inverted else (ids[0], None)

    def make_matte_source(self, node, ids=None, want_inverted=None):
        """the matte layer's own drawing, stripped of its paints: the clip geometry. + an inverted copy when needed.
        `ids` / `want_inverted`: a Set Matte's own source (not the layer's track-matte ids)"""
        cb = self.cb
        own_ids = ids is not None
        if ids is None:
            ids = cb.matte_src.get(self.L.index)
        if ids is None:
            ids = (self.conv.ids.key("comp", cb.kid, "L", self.L.id, "mattesrc"),
                   self.conv.ids.key("comp", cb.kid, "L", self.L.id, "mattesrcinv"))
            cb.matte_src[self.L.index] = ids
        # the emitted drawables live in `node`: strip paints / drop what cannot clip
        removed = set()
        from .rml import prop_key
        pk_th = prop_key("Stroke", "thickness")
        pk_v = prop_key("ScriptInputNumber", "propertyValue")

        def stroke_on(c):
            return c.tag == "Stroke" and ((c.id, pk_th) in self.anim.tracks or tonum(c.attrs.get("thickness", 1)) > 1e-6)

        def outline(c):
            """a stroke -> transparent Fill: its trims / dashes, then ae_outline (the painted band as a filled path)"""
            f = E("Fill", id=self.id("mattestroke", c.id), name=c.name + " · outline")
            f.add(E("SolidColor", id=self.id("mattestroke", c.id, "c"), name="Transparent", colorValue="00000000"))
            for x in c.children:
                if x.tag in ("TrimPath", "DashPath", "ScriptedPathEffect", "TargetEffect"):
                    f.add(x)
                else:
                    removed.update(y.id for y in x.iter() if y.id)
            fx = f.add(E("ScriptedPathEffect", id=self.id("mattestroke", c.id, "fx"), name="Stroke outline",
                         scriptAssetId=self.conv.script("ae_outline")))
            wi = fx.add(E("ScriptInputNumber", id=self.id("mattestroke", c.id, "w"), name="width",
                          propertyValue=float(tonum(c.attrs.get("thickness", 1)))))
            tr = self.anim.tracks.get((c.id, pk_th))
            if tr is not None:
                import copy
                self.anim.tracks[(wi.id, pk_v)] = copy.deepcopy(tr)
                self.anim.order.append((wi.id, pk_v))
            cap = {"round": 1.0, "square": 2.0}.get(c.attrs.get("cap", "butt"), 0.0)
            fx.add(E("ScriptInputNumber", id=self.id("mattestroke", c.id, "cap"), name="cap", propertyValue=cap))
            removed.add(c.id)
            return f

        def strip(e):
            keep = []
            # a stroked matte clips with the stroke's band (AE: the matte is the painted alpha); its fills then have to
            # stay as transparent fills too, or only the band would clip
            banded = any(stroke_on(c) for c in e.children)
            if banded:
                self.note("converted", self.name, "stroke in a track matte → its painted band clips (Luau ae_outline)")
            for c in e.children:
                if banded and stroke_on(c):
                    keep.append(outline(c))
                    continue
                if c.tag == "Fill" and (banded or any(x.tag in ("ScriptedPathEffect", "TargetEffect") for x in c.iter())):
                    # a clip follows its source's path effects (measured, CLI 1.2.0): keep the fill, transparent, so a
                    # 3D projection or a Zig Zag / Twist … still shapes the matte
                    for x in list(c.children):
                        if x.tag in ("SolidColor", "LinearGradient", "RadialGradient"):
                            removed.update(y.id for y in x.iter() if y.id)
                            c.children.remove(x)
                    c.children.insert(0, E("SolidColor", id=self.id("mattefx", c.id), name="Transparent",
                                           colorValue="00000000"))
                    keep.append(c)
                    continue
                if c.tag in ("Fill", "Stroke"):
                    removed.update(x.id for x in c.iter() if x.id)
                    continue
                if c.tag == "NestedArtboard":
                    # a precomp as matte: its shapes, inlined here with their keys moved to this timeline, clip
                    inl = self.inline_nested(c)
                    removed.update(x.id for x in c.iter() if x.id)
                    if inl is not None:
                        strip(inl)
                        keep.append(inl)
                        continue
                    self.note("approx", self.name, "precomp in a track matte could not be inlined: ignored")
                    continue
                if c.tag == "Image":
                    # Rive clips with paths only: the image's rectangle clips instead of its alpha. Dropped, it left
                    # the matte empty and hid what it mattes (broadcast-test's zootrope: every ice cream, matted by the
                    # same picture)
                    size = self.conv.asset_size(c.attrs.get("assetId"))
                    removed.update(x.id for x in c.iter() if x.id)
                    if size:
                        box = E("Shape", id=self.id("matteimg", c.id), name=c.name + " · rectangle")
                        for a in ("x", "y", "scaleX", "scaleY", "rotation"):
                            if a in c.attrs:
                                box.set(a, c.attrs[a])
                        box.add(E("Rectangle", id=self.id("matteimg", c.id, "r"), name="Image bounds",
                                  width=float(size[0]), height=float(size[1]),
                                  originX=float(c.attrs.get("originX", 0.5)), originY=float(c.attrs.get("originY", 0.5))))
                        for a in ("x", "y", "scaleX", "scaleY", "rotation"):
                            self.move_track(c, box, a)
                        keep.append(box)
                        self.note("approx", self.name, "image in a track matte: its rectangle clips (Rive clips with "
                                                       "paths, the image's alpha is not traced)")
                    else:
                        self.note("approx", self.name, "Image in a track matte cannot clip in Rive: ignored")
                    continue
                if c.tag == "Text":
                    removed.update(x.id for x in c.iter() if x.id)
                    self.note("approx", self.name, f"{c.tag} in a track matte cannot clip in Rive: ignored")
                    continue
                strip(c)
                if c.tag == "Solo" and not c.children:
                    # a video's frame Solo left empty: its KeyFrameId keys would name the dropped images
                    removed.add(c.id)
                    continue
                keep.append(c)
            e.children = keep
        strip(node)
        self.drop_tracks(removed)
        node.id = ids[0]
        users = cb.matte_users.get(self.L.index, []) if not own_ids else []
        if want_inverted or any(int(u.track_matte_type) in (5014, 5016) for u in users):
            # "everything minus the matte" (even-odd). The big rectangle sits at the artboard root, OUTSIDE the matte
            # layer's transforms: under them, a matte scaled to 0 took the rectangle with it and the inverted matte
            # hid everything (rig-test PL10: the teal half vanished when its "Subtract - rond" circle shrank to 0 —
            # AE shows the whole layer). The layer's ancestor chain is copied (keys included) above the geometry.
            inv = E("Node", id=ids[1], name=self.name + " · matte source (inverted)")
            inv.add(E("Shape", id=self.id("mattebig"), name="Everything")).add(
                E("Rectangle", id=self.id("mattebig", "r"), name="Everything", width=BIG, height=BIG))
            cur = inv
            for anc in self._chain_to(node):
                cur = cur.add(self.copy_frame(anc))
            cur.add(self.clone(node, "inv"))
            self.cb.ab.add(inv)
        if not own_ids:
            self.note("converted", self.name, "used as a track matte: its geometry is the clip source (not drawn)")

    def stencil_from_collapsed(self, nested, cb2):
        """a collapsed precomp's stencil / silhouette sources, copied into this comp (keys moved to its timeline)
        under copies of the precomp layer's own frames -> (clip source id, silhouette) for the layers placed after
        it, or None"""
        sid, silhouette = cb2.stencils[-1]
        # an invisible precomp layer cuts nothing (broadcast-test: three format variants of Pl04-anim, two at opacity 0 by
        # a menu expression — their skulls emptied the visible one)
        op = self.prop(self.tprop("ADBE Opacity"), "opacity")
        try:
            if op.animated:
                if all(tonum(unwrap(op.at(f / self.tl.fps))) <= 1e-6 for f in range(self.tl.nframes + 1)):
                    return None
            elif tonum(op.static(None, 100.0)) <= 1e-6:
                return None
        except Exception:
            pass
        inl = self.inline_nested(nested, only=[sid])
        if inl is None or not inl.children:
            return None
        cur = self.cb.ab.add(E("Node", id=self.conv.ids.new(), name=f"{self.name} · collapsed stencil"))
        for anc in self._chain_to(nested):
            cur = cur.add(self.copy_frame(anc))
        cur.add(inl)
        src = next((e for e in inl.iter() if e.name and e.name.endswith(("matte source (inverted)", "matte source"))
                    and e.tag == "Node"), None)
        target = src if src is not None else inl
        self.note("converted", self.name, "collapsed precomp: its stencil / silhouette cuts the layers below it here too")
        return target.id, silhouette

    def inline_nested(self, nested, only=None):
        """copy of a NestedArtboard's artboard content (fresh ids) whose keys are moved from the precomp's timeline
        to this one, through the nested time (NestedRemapAnimation.time, keyed from the layer's start / time
        remap): what the precomp shows is then geometry of THIS artboard, usable as a clip source"""
        from .rml import Track, prop_key
        aid = nested.attrs.get("artboardId")
        cb2 = next((b for b in self.conv.builds.values() if b.ab.id == aid), None)
        if cb2 is None:
            return None
        fps_c = float(cb2.anim.fps)
        D = max(1e-6, cb2.anim.duration / fps_c)
        fps_p = float(self.anim.fps)
        remap = next((x for x in nested.children if x.tag == "NestedRemapAnimation"), None)
        off = 0.0                                  # parent time = child time + off (seconds)
        if remap is not None:
            tr = self.anim.tracks.get((remap.id, prop_key("NestedRemapAnimation", "time")))
            if tr is not None and tr.keys:
                f0, frac0 = tr.keys[0][0], float(tr.keys[0][1])
                off = f0 / fps_p - frac0 * D
            else:
                off = -float(remap.attrs.get("time", 0.0) or 0.0) * D
        mapping = {}

        def cp(e):
            n = E(e.tag, id=None, name=e.name)
            n.attrs = dict(e.attrs)
            if e.id is not None:
                n.id = self.conv.ids.new()
                mapping[e.id] = n.id
            n.children = [cp(c) for c in e.children]
            return n
        out = E("Node", id=self.conv.ids.new(), name=f"{nested.name} · inlined matte")
        if only is None:
            out.children = [cp(c) for c in cb2.ab.children if c.tag not in ("Fill", "LinearAnimation", "StateMachine")]
        else:
            # only these elements, each under plain copies of its ancestors' transforms
            def path_to(e, target, acc):
                for c in e.children:
                    if c.id == target:
                        return acc + [c]
                    r = path_to(c, target, acc + [c])
                    if r is not None:
                        return r
                return None
            for target in only:
                chain = path_to(cb2.ab, target, [])
                if not chain:
                    continue
                cur = out
                for anc in chain[:-1]:
                    n = E("Node", id=self.conv.ids.new(), name=anc.name + " · copy")
                    n.attrs = {k: v for k, v in anc.attrs.items() if k in ("x", "y", "rotation", "scaleX", "scaleY")}
                    if anc.id is not None:
                        mapping[anc.id] = n.id
                    cur.add(n)
                    cur = n
                cur.add(cp(chain[-1]))
        for e in out.iter():
            for a in ("sourceId", "styleId"):
                if a in e.attrs and e.attrs[a] in mapping:
                    e.attrs[a] = mapping[e.attrs[a]]
        for (oid, pk) in list(cb2.anim.order):
            if oid in mapping:
                tr = cb2.anim.tracks[(oid, pk)]
                keys = [((k[0] / fps_c + off) * fps_p,) + tuple(k[1:]) for k in tr.keys]
                self.anim.tracks[(mapping[oid], pk)] = Track(tr.kind, keys)
                self.anim.order.append((mapping[oid], pk))
        self.note("converted", self.name, f"precomp '{nested.name}' as a track matte → its shapes inlined as the clip")
        return out

    def move_track(self, src, dst, attr):
        """copy the keys of src.attr onto dst.attr (same property key: both are transform components)"""
        import copy
        from .rml import prop_key
        k = (src.id, prop_key(src.tag, attr))
        tr = self.anim.tracks.get(k)
        if tr is not None:
            nk = (dst.id, prop_key(dst.tag, attr))
            self.anim.tracks[nk] = copy.deepcopy(tr)
            self.anim.order.append(nk)

    def _container_of(self, node):
        for e in self.cb.ab.iter():
            if node in e.children:
                return e
        return self.cb.ab

    def _chain_to(self, node):
        """the artboard's descendants that contain `node`, outermost first (node excluded)"""
        def walk(e, acc):
            for c in e.children:
                if c is node:
                    return acc
                r = walk(c, acc + [c])
                if r is not None:
                    return r
            return None
        return walk(self.cb.ab, []) or []

    def copy_frame(self, el):
        """a container's transform alone (no children), fresh id, its keys duplicated: a Solo or other container
        becomes a plain Node with the same transform"""
        import copy
        from .rml import prop_key
        n = E("Node", id=self.conv.ids.new(), name=el.name + " · copy")
        keep = ("x", "y", "rotation", "scaleX", "scaleY") if el.tag != "Node" else None
        n.attrs = {k: v for k, v in el.attrs.items() if keep is None or k in keep}
        pks = None if keep is None else {prop_key("Node", k) for k in keep}
        for (oid, pk) in list(self.anim.order):
            if oid == el.id and (pks is None or pk in pks):
                k = (n.id, pk)
                self.anim.tracks[k] = copy.deepcopy(self.anim.tracks[(oid, pk)])
                self.anim.order.append(k)
        return n

    # ---------------------------------------------------------------- effects
    def layer_styles(self):
        """Layer Styles (drop shadow, glow, stroke…) are not effects: py-aep lists them under 'ADBE Layer Styles'.
        Not in Rive; reported so a missing shadow is not a surprise (Infinity's Web Elements card)."""
        try:
            ls = self.L.property("ADBE Layer Styles")
        except Exception:
            ls = None
        if ls is None or not getattr(ls, "enabled", False):
            return
        from .aexpr import _children
        on = [clean(g.name) for g in _children(ls)
              if getattr(g, "enabled", False) and "/" in getattr(g, "match_name", "")]
        if on:
            self.note("unsupported", self.name, f"layer style(s) not converted: {', '.join(on)}")

    def effects_plan(self):
        L = self.L
        self.layer_styles()
        plan = {"wrappers": [], "colors": [], "comments": [], "ramps": [], "setmattes": [], "copyfrom": None, "shadows": []}
        try:
            fxs = list(L.effects.properties) if L.effects is not None else []
        except Exception:
            fxs = []
        for fx in fxs:
            if not getattr(fx, "enabled", True):
                continue
            mn = getattr(fx, "match_name", "")
            if mn in CONTROL_FX or mn.startswith("Pseudo/"):
                continue
            if mn == "ADBE Geometry2":
                plan["wrappers"].append(fx)
                continue
            if mn == "ADBE Drop Shadow" and type(L).__name__ in ("ShapeLayer", "TextLayer"):
                plan["shadows"].append(fx)
                continue
            if mn in BLUR_FX and type(L).__name__ in ("ShapeLayer", "TextLayer") and not plan.get("shadows") \
                    and not plan.get("glows"):
                plan.setdefault("blurs", []).append(fx)
                continue
            if mn == "ADBE Glo2" and type(L).__name__ in ("ShapeLayer", "TextLayer"):
                plan.setdefault("glows", []).append(fx)
                continue
            if mn == "ADBE Posterize Time":
                from .keys import posterize_fps
                self.note("converted", clean(fx.name), f"Posterize Time {posterize_fps(L) or 0:g} fps → the layer's keys "
                                                       "held at that rate (transform included, as AE does; a precomp's own time is not stepped)")
                continue
            if mn == "ADBE Corner Pin" and not getattr(L, "adjustment_layer", False) and "cornerpin" not in plan:
                plan["cornerpin"] = fx
                continue
            if mn == "ADBE Ramp" and type(L).__name__ in ("ShapeLayer", "TextLayer") or (
                    mn == "ADBE Ramp" and type(L).__name__ == "AVLayer" and
                    type(getattr(getattr(L, "source", None), "main_source", None)).__name__ == "SolidSource"):
                plan["ramps"].append(fx)
                continue
            if mn == "ADBE Set Matte3":
                sm = self.set_matte_of(fx)
                if sm is not None:
                    plan["setmattes"].append(sm)
                    continue
            if mn == "ADBE Compound Arithmetic":
                src = self.fx_layer(fx, 1)
                P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in self.fx_params(fx)}
                op = int(tonum(P[2].value)) if 2 in P else 0
                if src is not None and op == 1 and self.cb.drawn(src):
                    # "Copy" puts the second layer's pixels in this one: where it also shows (Set Matte from the same
                    # layer) the result repeats a layer AE draws anyway (broadcast-test's guitar "Zone": a red ellipse)
                    plan["copyfrom"] = src
                    continue
            if mn in COLOR_FX and mn in fxlib_effects() and self._raster_content():
                # a colour effect on pixels (footage, or a precomp holding footage): recolouring vector paints would
                # leave the image untouched (measured: Fill on a PNG layer stayed unfilled) -> the WGSL node
                plan.setdefault("wgsl", []).append(fx)
                continue
            if mn in COLOR_FX:
                fn = self.color_fn(fx)
                if fn is not None:
                    plan["colors"].append((fx, fn))
                    self.note("converted", clean(fx.name), f"{mn} applied to the layer's colours (vector: exact)")
                    continue
            if mn in fxlib_effects():
                plan.setdefault("wgsl", []).append(fx)
                continue
            plan["comments"].append(self.effect_comment(fx))
            self.todo(fx, adjustment=False)
        # colour effects of the precomp layers above this comp (outermost last): a Hue/Saturation on an FX pack's
        # precomp recolours everything inside (broadcast-test: Bubble_29 pink)
        plan["colors"] += list(self.cb.inherited_colors)
        self._color_plan = plan["colors"]
        return plan

    def _raster_content(self, src=None, depth=0):
        """the layer draws pixels (footage / image sequence / video), directly or inside its precomp"""
        if src is None:
            if type(self.L).__name__ != "AVLayer":
                return False
            src = getattr(self.L, "source", None)
        if src is None or depth > 8:
            return False
        if hasattr(src, "layers"):
            for L in src.layers:
                if type(L).__name__ == "AVLayer" and getattr(L, "enabled", True) and not getattr(L, "adjustment_layer", False) \
                        and self._raster_content(getattr(L, "source", None), depth + 1):
                    return True
            return False
        ms = getattr(src, "main_source", None)
        if type(ms).__name__ == "SolidSource" or not getattr(src, "width", 0):
            return False
        f = (getattr(ms, "file", "") or "").lower()
        return not f.endswith(AUDIO_EXT)

    def color_fn_at(self, t):
        """the pending colour effects frozen at comp time t: rgb -> rgb"""
        fns = [fn for _, fn in (getattr(self, "_color_plan", None) or [])]

        def f(rgb):
            for g in fns:
                rgb = g(rgb, t)
            return rgb
        return f

    def color_animated(self, frames):
        probes = ([0.5, 0.5, 0.5], [0.9, 0.2, 0.4], [0.1, 0.7, 0.9])
        ref = None
        for fr in frames:
            fn = self.color_fn_at(fr / self.tl.fps)
            out = [tuple(round(float(x), 4) for x in fn(list(c))) for c in probes]
            if ref is None:
                ref = out
            elif out != ref:
                return True
        return False

    def pending_color_fn(self):
        plan = getattr(self, "_color_plan", None)
        if not plan:
            return None
        fns = [fn for _, fn in plan]

        def comp_fn(rgb):
            for f in fns:
                rgb = f(rgb, 0.0)
            return rgb
        return comp_fn

    def fx_layer(self, fx, i):
        """the layer a layer parameter (-000i) of an effect points at (value = 1-based layer index), or None"""
        P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in self.fx_params(fx)}
        p = P.get(i)
        if p is None:
            return None
        try:
            idx = int(round(tonum(p.value))) - 1
        except Exception:
            return None
        layers = self.cb.layers
        return layers[idx] if 0 <= idx < len(layers) and layers[idx] is not self.L else None

    def set_matte_of(self, fx):
        """ADBE Set Matte3 -> (source layer, inverted) when it is an alpha / luminance matte from a layer"""
        P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in self.fx_params(fx)}
        src = self.fx_layer(fx, 1)
        if src is None:
            return None
        use = int(tonum(P[2].value)) if 2 in P else 4
        if use not in (4, 5):
            return None
        if use == 5:
            self.note("approx", clean(fx.name), "Set Matte from luminance treated as alpha")
        return src, bool(int(tonum(P[3].value))) if 3 in P else False

    def set_matte_clips(self, holder, sms):
        """Set Matte: the layer keeps only where the source layer is, the source drawn as it is in the comp — its
        own transform and its parents' (measured in AE 26.5 on shape layers: a moved / scaled / rotated source
        clips where it sits in the comp, and the target's own rotation does not turn the matte). The source is
        rebuilt at the artboard root with its transform chain, paints stripped. -> the node the content goes into.
        (Clipped in the target's own space with the untransformed source — the first try — Infinity's Web Elements
        lost its texts and its "+" button.)"""
        node = holder
        cb = self.cb
        for k, (src, inv) in enumerate(sms):
            lb2 = LayerBuild(cb, src)
            lb2.copy_suffix = ["setmatte", self.L.id, k]
            root = cb.ab.add(E("Node", id=lb2.id("smroot"), name=f"{self.name} · set matte source {clean(src.name)}"))
            cur = root
            for j, P in enumerate(cb.chain(src) + [src]):
                lbp = LayerBuild(cb, P)
                lbp.copy_suffix = ["setmatte", self.L.id, k, j]
                fr, inner = lbp.frame(1)
                cur.add(fr)
                cur = inner
            geo = cur.add(E("Node", id=lb2.id("smgeo"), name=f"{self.name} · set matte {clean(src.name)}"))
            lb2.emit_drawables(geo)
            ids = (lb2.id("smsrc"), lb2.id("smsrcinv"))
            lb2.make_matte_source(geo, ids=ids, want_inverted=inv)
            clip = E("Node", id=self.id("setmatte", k), name=f"{self.name} · set matte")
            clip.add(E("ClippingShape", id=self.id("setmatte", k, "c"), name="Set Matte",
                       sourceId=ids[1] if inv else ids[0], fillRule="evenOdd" if inv else None))
            node.add(clip)
            node = clip
            self.note("converted", self.name, f"Set Matte from '{clean(src.name)}' → clipping by its shapes")
        return node

    def geo_point_scale(self, *aps):
        """1, or 0.01 when py-aep reads a Transform effect's anchor / position 100 times too large (measured on
        promo-test: AE shows [960, 540], py-aep [96000, 54000], even after a re-save in AE 2026; a fresh AE 2026
        project reads right): the values then lie far outside the layer and land inside it once divided by 100"""
        w, h = self.source_size()
        big = 10.0 * max(w, h, 1.0)
        vals = []
        for ap in aps:
            if ap is None:
                continue
            try:
                v = unwrap(ap.at(0.0)) if ap.animated else ap.static(None, [0, 0])
                vals += [abs(tonum(x)) for x in (list(v) if isinstance(v, (list, tuple)) else [v])[:2]]
            except Exception:
                pass
        if vals and max(vals) > big and max(vals) / 100.0 <= big:
            self.note("approx", self.name, "Transform effect anchor / position read 100× too large (py-aep): divided by 100")
            return 0.01
        return 1.0

    def adjustment_transform(self, geo, parent):
        """Transform effects of an adjustment layer -> nested nodes under `parent`, identity outside the layer's
        in/out (sampled per frame). -> the innermost node"""
        L, tl = self.L, self.tl
        in_f = int(round(float(L.in_point) * tl.fps))
        out_f = int(round(float(L.out_point) * tl.fps))
        holder = parent
        for k, fx in enumerate(geo):
            P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in self.fx_params(fx)
                 if getattr(p, "match_name", "").startswith("ADBE Geometry2-")}

            def ap(i):
                return self.prop(P[i], f"adjustment transform {i}") if P.get(i) is not None else None
            anc, pos, sh, sw, rot, op = ap(1), ap(2), ap(3), ap(4), ap(7), ap(8)
            ks = self.geo_point_scale(anc, pos)
            uni = P.get(11)
            uniform = bool(tonum(uni.value)) if uni is not None else True
            node = holder.add(E("Node", id=self.id("adjfx", k), name=f"{self.name} · {clean(fx.name)}"))
            inner = node.add(E("Node", id=self.id("adjfx", k, "a"), name=f"{self.name} · {clean(fx.name)} · anchor"))
            dense = {"x": [], "y": [], "scaleX": [], "scaleY": [], "rotation": [], "opacity": [], "ax": [], "ay": []}
            for f in range(tl.nframes + 1):
                t = f / tl.fps
                if in_f <= f < out_f:
                    pv = unwrap(pos.at(t)) if pos else [0, 0]
                    av = unwrap(anc.at(t)) if anc else [0, 0]
                    w = tonum(unwrap(sw.at(t))) / 100 if sw else 1.0
                    h = tonum(unwrap(sh.at(t))) / 100 if sh else 1.0
                    vals = (tonum(pv[0]) * ks, tonum(pv[1]) * ks, h if uniform else w, h,
                            tonum(unwrap(rot.at(t))) * math.pi / 180 if rot else 0.0,
                            tonum(unwrap(op.at(t))) / 100 if op else 1.0, tonum(av[0]) * ks, tonum(av[1]) * ks)
                else:
                    vals = (0.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0)
                for key, v in zip(("x", "y", "scaleX", "scaleY", "rotation", "opacity", "ax", "ay"), vals):
                    dense[key].append((f, v))
            for key in ("x", "y", "scaleX", "scaleY", "rotation", "opacity"):
                self.put_dense(node, key, dense[key])
            self.put_dense(inner, "x", [(f, -v) for f, v in dense["ax"]])
            self.put_dense(inner, "y", [(f, -v) for f, v in dense["ay"]])
            skew = P.get(5)
            if skew is not None and abs(tonum(skew.value)) > 1e-6:
                self.note("approx", clean(fx.name), "Transform effect skew is not in Rive: ignored")
            holder = inner
        self.note("converted", self.name, "adjustment layer Transform effect → nodes around every layer below it")
        return holder

    # ---------------------------------------------------------------- raster effects as WGSL (fxlib)
    def fx_node(self, fxs, sub, key, amount=None, pad=0.0):
        """ONE ScriptedDrawable running this layer's fxlib effect stack `fxs` (AE order) over the artboard `sub`"""
        name = f"{self.name} · " + " + ".join(clean(fx.name) for fx in fxs)
        sd = E("ScriptedDrawable", id=self.id("wgsl", *key), name=name)
        return self.cb.fill_fx_node(sd, [{"lb": self, "fxs": fxs, "amount": amount, "blend": 0}], sub, self, key, pad)

    def adjustment_group(self, fxs):
        """this adjustment layer as a group of an effect node: its effects, its opacity × in/out window (amount, dense
        per frame, None when always 1) and its blend mode (_ae_fx_mix.wgsl code)"""
        from .fxlib import MIX_BLEND
        L = self.L
        n, fps = self.tl.nframes, float(self.tl.fps)
        in_f = int(round(float(L.in_point) * fps))
        out_f = int(round(float(L.out_point) * fps))
        op = self.prop(self.tprop("ADBE Opacity"), "opacity")
        amount = []
        for f in range(n + 1):
            on = in_f <= f < out_f
            o = tonum(unwrap(op.at(f / fps))) if op.animated else tonum(op.static(None, 100))
            amount.append((f, (o / 100.0) if on else 0.0))
        if all(abs(a - 1.0) < 1e-6 for _, a in amount):
            amount = None
        try:
            bm = getattr(L.blending_mode, "name", None) or str(L.blending_mode)
        except Exception:
            bm = "NORMAL"
        blend = MIX_BLEND.get(bm)
        if blend is None:
            blend = 0
            self.note("approx", self.name, f"adjustment layer blend mode {bm}: applied as normal")
        lib = fxlib_effects()
        for fx in fxs:
            m = lib[fx.match_name]
            self.note("approx" if m.get("status") in ("approx", "unverified") else "converted", clean(fx.name),
                      f"adjustment layer {fx.match_name} → WGSL ({m['slug']}, {m.get('status', 'unverified')} vs AE) over "
                      f"the layers below (opacity / in-out → mix" + (f", blend {bm.lower()}" if blend else "") + ")")
        if self.mask_items():
            self.note("approx", self.name, "adjustment layer masks are not applied to its WGSL effects")
        return {"lb": self, "fxs": fxs, "amount": amount, "blend": blend}

    def fx_grow(self, fxs):
        """canvas margin (px) an effect stack spills into: blurs / glows / shadows add up (AE grows the layer's buffer)"""
        lib = fxlib_effects()
        return float(min(1024, sum(self._fx_grow1(lib[fx.match_name], fx) for fx in fxs)))

    def _fx_grow1(self, m, fx):
        from .fxlib import param_match_name
        P = {getattr(q, "match_name", ""): q for q in self.fx_params(fx)}

        def v(i, d=0.0):
            try:
                return abs(tonum(unwrap(P[param_match_name(fx.match_name, i)].value)))
            except Exception:
                return d
        slug = m["slug"]
        if slug == "gaussian_blur":
            g = 3 * 0.283 * v(1)
        elif slug in ("gaussian_blur_legacy", "fast_blur_legacy"):
            g = 3 * 0.370 * v(1)                      # three boxes of radius 0.370 x blurriness (measured)
        elif slug == "box_blur":
            g = v(1) * max(1.0, v(2, 3.0))
        elif slug == "glow":
            g = 1.2 * v(3, 10.0)
        elif slug == "drop_shadow":
            g = v(4, 5.0) + 3 * v(5) / 4.6
        elif slug in ("directional_blur", "minimax", "simple_choker"):
            g = v(2)
        else:
            g = 0.0
        return float(min(512, math.ceil(g)))

    def wgsl_layer(self, fxs, masked):
        """the layer's drawing goes into a sub-artboard (layer size, layer space), filtered by the effect nodes in
        stack order (the first effect innermost); the opacity window / matte / transform stay on the layer's nodes"""
        lib = fxlib_effects()
        if three.is3d(self.L):
            for fx in fxs:
                self.note("approx", clean(fx.name), "raster effect on a 3D layer: not converted (WGSL needs a flat layer)")
                self.todo(fx, adjustment=False)
            return
        holder = getattr(self, "_draw_holder", None)
        if holder is None:
            return
        kids = [c for c in holder.children[getattr(self, "_draw_before", 0):] if c.tag != "ClippingShape"]
        if not kids:
            return
        for c in kids:
            holder.children.remove(c)
            if c.attrs.get("blendModeValue"):
                c.attrs.pop("blendModeValue", None)
        w, h = self.source_size()
        sub = FxArtboard(self.cb, (self.L.id, "layer"), f"{self.cb.scope} · {self.name} · effects", w, h)
        for c in kids:
            sub.ab.add(c)
        node = self.fx_node(fxs, sub, ("layer",), pad=self.fx_grow(fxs))
        for fx in fxs:
            m = lib[fx.match_name]
            self.note("approx" if m.get("status") in ("approx", "unverified") else "converted", clean(fx.name),
                      f"{fx.match_name} → WGSL ({m['slug']}, {m.get('status', 'unverified')} vs AE) in the layer's effect "
                      f"node ({len(fxs)} effect{'s' if len(fxs) > 1 else ''}, AE order) over a sub-artboard of its content")
        if self.blend and self.blend != "srcOver":
            node.set("blendModeValue", self.blend)
        holder.add(node)
        self.drawables = [node]
        if masked:
            self.note("approx", self.name, "masks clip after the raster effect (AE: masks first, then effects)")

    def adjustment_note(self, skip=()):
        L = self.L
        try:
            fxs = [fx for fx in L.effects.properties if getattr(fx, "enabled", True)]
        except Exception:
            fxs = []
        real = [fx for fx in fxs if getattr(fx, "match_name", "") not in CONTROL_FX and not getattr(fx, "match_name", "").startswith("Pseudo/")
                and getattr(fx, "match_name", "") not in skip]
        for fx in real:
            self.todo(fx, adjustment=True)
        if real:
            self.note("unsupported", self.name, f"adjustment layer ({', '.join(getattr(fx, 'match_name', '') for fx in real)}): "
                                                "needs a post-process (WGSL) — see effects_todo.json")

    def todo(self, fx, adjustment):
        params, animated = {}, []
        for p in self.fx_params(fx):
            mn = getattr(p, "match_name", "")
            try:
                v = to_js(p.value)
            except Exception:
                v = None
            if isinstance(v, (int, float, list, str)) and not isinstance(v, bool):
                params[f"{mn} ({clean(p.name)})"] = v
            try:
                if len(p.keyframes) > 1 or self.conv.engine.has_expr(p):
                    animated.append(clean(p.name))
            except Exception:
                pass
        self.conv.report.todo_effects.append({"comp": self.scope, "layer": self.name, "matchName": getattr(fx, "match_name", ""),
                                             "name": clean(fx.name), "adjustment": adjustment, "params": params,
                                             "animated": animated, "inPoint": float(self.L.in_point),
                                             "outPoint": float(self.L.out_point)})

    # measured (AE 26.5, 200×120 rectangle, edge repeat off): Gaussian σ = 0.283 × blurriness (10/20/40 -> 2.83/5.65/
    # 11.26); Fast Box Blur r10×3 / r20×3 -> σ 10.8 / 21.0 = r·sqrt(n·(1 + 1/r) / 3), r20×1 -> 14.0 = 0.34·(2r + 1).
    # Rive Feather strength = 2 σ.
    BLUR_SIGMA_K = 0.283

    def blur(self, fx):
        """AE Gaussian / Fast Box / Box Blur on a vector layer -> a Feather on every paint of the layer (keyed when the
        blurriness is), fills under the clockwise rule. Blurs placed BEFORE a drop shadow / glow only (stack order).
        'Repeat Edge Pixels' (ON by default in AE 2026) clips the blur to the layer bounds: the outline stays crisp."""
        mn = fx.match_name
        P = {int(getattr(q, "match_name", "0-0").split("-")[-1]): q for q in self.fx_params(fx)}
        box = mn in ("ADBE Box Blur2", "ADBE Box Blur")
        legacy = mn == "ADBE Gaussian Blur"
        defaults = ({1: 0, 2: 3, 3: 1, 4: 1} if box else {1: 0, 2: 1} if legacy else {1: 25, 2: 1, 3: 1})
        q_amt = P.get(1)
        dims_i = 3 if box else 2
        edge_i = 4 if box else (None if legacy else 3)

        def static(i):
            q = P.get(i)
            d = defaults.get(i, 0)
            if q is None:
                return d
            try:
                ap = self.prop(q, f"blur {i}")
                return tonum(unwrap(ap.at(0.0)) if ap.animated else ap.static(None, d))
            except Exception:
                return d
        if edge_i and int(static(edge_i)):
            self.note("approx", clean(fx.name), "'Repeat Edge Pixels' on: AE keeps the layer's outline crisp (measured) — "
                                               "no feather; edges inside the layer bounds are not blurred in Rive")
            return
        if box:
            n = max(1, int(static(2)))
            r = max(static(1), 1.0)
            k = 2 * (0.34 * (2 + 1 / r) if n == 1 else math.sqrt(n * (1 + 1 / r) / 3))
        else:
            k = 2 * self.BLUR_SIGMA_K
        if q_amt is None:
            return
        ap = self.prop(q_amt, "blurriness")
        if not ap.animated and static(1) <= 0:
            return
        dims = int(static(dims_i))
        if dims != 1:
            self.note("approx", clean(fx.name), "one-axis blur: Rive feathers both axes")
        paints = [e for d in self.drawables if d.tag not in ("Image", "NestedArtboard", "Solo")
                  for e in d.iter() if e.tag in ("Fill", "Stroke")]
        evenodd = False
        for e in paints:
            if any(c.tag == "Feather" for c in e.children):
                continue
            fe = E("Feather", id=self.conv.ids.new(), name=clean(fx.name))
            e.add(fe)
            if ap.animated:
                self.put(fe, "strength", ap, None, a=k, b=0.0, force=True)
            else:
                fe.set("strength", float(k * static(1)))
            if e.tag == "Fill":
                evenodd = evenodd or e.attrs.get("fillRule") == "evenOdd"
                e.set("fillRule", "clockwise")
        if not paints:
            self.note("approx", clean(fx.name), "blur on a layer without vector paints: not converted")
            return
        self.note("converted", clean(fx.name), f"{'Box' if box else 'Gaussian'} blur → Feather on {len(paints)} paint(s) "
                                               f"(strength = {k:.3g} × {'radius' if box else 'blurriness'})")
        if evenodd:
            self.note("approx", clean(fx.name), "even-odd shapes: their feathered fills use the clockwise rule")

    SHADOW_SOFT_K = 0.43        # measured: AE Drop Shadow softness ≈ 4.6 σ ; Rive Feather strength = 2 σ

    def drop_shadow(self, fx, holder):
        """AE Drop Shadow effect (vector layers) -> a copy of the layer's drawables under them, every paint turned into
        the shadow colour and feathered (Rive 1.3: Feather in a Fill under fillRule clockwise), offset in the layer's
        space (AE renders effects before the layer transform: the shadow turns with the layer)"""
        P = {int(getattr(q, "match_name", "0-0").split("-")[-1]): q for q in self.fx_params(fx)}

        from .shapes import _stored

        def val(i, d):
            q = P.get(i)
            if q is None or not _stored(q):
                return d                    # not stored: AE's default (135°, 5 px, 50 %…), not py-aep's 0
            ap = self.prop(q, f"drop shadow {i}")
            if ap.animated:
                self.note("approx", clean(fx.name), "animated drop shadow parameter: its first value is used")
            return unwrap(ap.at(0.0)) if ap.animated else ap.static(None, d)
        col = val(1, [0, 0, 0, 1])
        col = list(col) if isinstance(col, (list, tuple)) else [0, 0, 0, 1]
        opa = tonum(val(2, 127.5)) / 255.0
        ang = math.radians(tonum(val(3, 135.0)))
        dist = tonum(val(4, 5.0))
        soft = tonum(val(5, 0.0))
        only = bool(tonum(val(6, 0)))
        dx, dy = dist * math.sin(ang), -dist * math.cos(ang)
        srcs = [d for d in self.drawables if d.tag not in ("Image", "NestedArtboard", "Solo")]
        if not srcs:
            self.note("approx", clean(fx.name), "drop shadow on a layer without vector drawables: not converted")
            return
        wrap = E("Node", id=self.id("dshadow", fx.name), name=f"{self.name} · drop shadow", x=dx, y=dy)
        removed = set()
        evenodd = False
        for d in srcs:
            c = self.clone(d, "dshadow")
            c.name = d.name + " · shadow"
            for e in list(c.iter()):
                if e.tag not in ("Fill", "Stroke"):
                    continue
                for x in list(e.children):
                    if x.tag in ("SolidColor", "LinearGradient", "RadialGradient", "Feather"):
                        removed.update(y.id for y in x.iter() if y.id)
                        e.children.remove(x)
                e.children.insert(0, E("SolidColor", id=self.conv.ids.new(), name="Shadow", colorValue=argb(col[:3], opa)))
                if soft > 1e-6:
                    e.add(E("Feather", id=self.conv.ids.new(), name="Softness", strength=max(0.01, self.SHADOW_SOFT_K * soft)))
                    if e.tag == "Fill":
                        evenodd = evenodd or e.attrs.get("fillRule") == "evenOdd"
                        e.set("fillRule", "clockwise")       # Rive draws a feathered fill under the clockwise rule only
            wrap.add(c)
        self.drop_tracks(removed)
        holder.add(wrap)                                         # last child: drawn under the whole layer
        if only:
            for d in srcs:
                d.set("hidden", True)
        self.note("converted", clean(fx.name), f"Drop Shadow → feathered copy (softness {soft:g} → feather "
                                               f"{self.SHADOW_SOFT_K * soft:.3g}, offset {dx:.3g}, {dy:.3g})")
        if evenodd:
            self.note("approx", clean(fx.name), "even-odd shapes: their feathered shadow fills by the clockwise rule")

    GLOW_RADIUS_K = 0.8   # fitted on the halo of AE Glow (radius 20 / 40: 0.15–0.18 % error outside the shape)

    def glow(self, fx, holder):
        """AE Glow (vector layers) -> a feathered copy of the layer's drawables ON TOP, blended by screen (Rive has no
        add). Colour channels: each channel glows by how far it passes the threshold, (c − t) / (1 − t) (an orange
        255,140,51 at 60 % glows red only — measured); alpha channel / A & B colours: the paint's colour / colour A."""
        P = {int(getattr(q, "match_name", "0-0").split("-")[-1]): q for q in self.fx_params(fx)}
        from .shapes import _stored
        defaults = {1: 2, 2: 153.0, 3: 10.0, 4: 1.0, 5: 2, 6: 3, 7: 1, 12: [1, 1, 1, 1], 13: [0, 0, 0, 1]}

        def val(i):
            q = P.get(i)
            d = defaults.get(i, 0)
            if q is None or not _stored(q):
                return d
            ap = self.prop(q, f"glow {i}")
            if ap.animated:
                self.note("approx", clean(fx.name), "animated glow parameter: its first value is used")
            return unwrap(ap.at(0.0)) if ap.animated else ap.static(None, d)
        based_alpha = int(tonum(val(1))) == 1
        thr = max(0.0, min(0.999, tonum(val(2)) / 255.0))
        radius = tonum(val(3))
        inten = max(0.0, tonum(val(4)))
        colors = int(tonum(val(7)))
        col_a = val(12)
        if int(tonum(val(5))) == 3:
            self.note("approx", clean(fx.name), "glow 'composite original: none': the original stays drawn")
        srcs = [d for d in self.drawables if d.tag not in ("Image", "NestedArtboard", "Solo")]
        if not srcs or radius <= 0:
            return

        def glow_of(argb_hex):
            a = int(argb_hex[0:2], 16) / 255.0
            rgb = [int(argb_hex[i:i + 2], 16) / 255.0 for i in (2, 4, 6)]
            if colors != 1 and isinstance(col_a, (list, tuple)):
                rgb = [float(x) for x in list(col_a)[:3]]
            elif not based_alpha:
                rgb = [max(0.0, (c - thr) / (1 - thr)) for c in rgb]
            rgb = [min(1.0, c * inten) for c in rgb]
            return argb(rgb, a)
        wrap = E("Node", id=self.id("glow", fx.name), name=f"{self.name} · glow")
        removed = set()
        n = 0
        for d in srcs:
            c = self.clone(d, "glow")
            c.name = d.name + " · glow"
            for e in list(c.iter()):
                if e.tag in ("Shape", "Text"):
                    e.set("blendModeValue", "screen")
                if e.tag not in ("Fill", "Stroke"):
                    continue
                src = next((x for x in e.children if x.tag == "SolidColor"), None)
                if src is None:
                    st = next((x for x in e.iter() if x.tag == "GradientStop"), None)
                    hexv = (st.attrs.get("colorValue") if st is not None else None) or "FFFFFFFF"
                else:
                    hexv = src.attrs.get("colorValue") or "FFFFFFFF"
                for x in list(e.children):
                    if x.tag in ("SolidColor", "LinearGradient", "RadialGradient", "Feather"):
                        removed.update(y.id for y in x.iter() if y.id)
                        e.children.remove(x)
                e.children.insert(0, E("SolidColor", id=self.conv.ids.new(), name="Glow", colorValue=glow_of(hexv)))
                e.add(E("Feather", id=self.conv.ids.new(), name="Glow radius", strength=max(0.01, self.GLOW_RADIUS_K * radius)))
                if e.tag == "Fill":
                    e.set("fillRule", "clockwise")
                n += 1
            wrap.add(c)
        self.drop_tracks(removed)
        first = next((i for i, x in enumerate(holder.children) if x in srcs), 0)
        holder.children.insert(first, wrap)                      # before the drawables: drawn over them
        self.note("approx", clean(fx.name), f"Glow → feathered copy on top, screen blend (radius {radius:g} → feather "
                                            f"{self.GLOW_RADIUS_K * radius:.3g}; AE adds, Rive screens)")

    def wipe_matte(self, own):
        """Linear Wipe(s) on a solid that only serves as a track matte (promo-test: the inverted matte of a video,
        wiped open): the matte's geometry becomes the solid's rectangle cut by the wipe's half-plane, keyed per frame.
        Rule measured on AE 2026 (fxref linear_wipe): travel direction d = (sin a, -cos a); the transparent part is
        where dot(p, d) < pmin + completion * (pmax - pmin), p and the extremes over the LAYER rect (40 % at 90° cuts
        a 640 px layer at x = 256 exactly). Feather is not reproduced (hard edge)."""
        L = self.L
        try:
            fxs = [fx for fx in (L.effects.properties if L.effects is not None else [])
                   if getattr(fx, "enabled", True) and getattr(fx, "match_name", "") == "ADBE Linear Wipe"]
        except Exception:
            fxs = []
        if not fxs:
            return
        rects = [e for e in own.iter() if e.tag == "Rectangle"]
        src = getattr(L, "source", None)
        solid = getattr(getattr(src, "main_source", None), "color", None) is not None
        if not solid or len(rects) != 1:
            self.note("approx", self.name, "Linear Wipe on a matte layer that is not a plain solid: the matte is not wiped")
            return
        from .aexpr import Env
        eng, comp = self.conv.engine, self.cb.comp
        env = Env(eng, None, L, comp, 0.0)
        w, h = self.source_size()
        params = []
        for fx in fxs:
            P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in self.fx_params(fx)
                 if getattr(p, "match_name", "").startswith("ADBE Linear Wipe-")}
            params.append(P)

        def val(P, i, t, d):
            p = P.get(i)
            if p is None:
                return d
            try:
                return tonum(unwrap(eng.value(p, L, comp, t, parent_env=env)))
            except Exception:
                return d
        env.dep = False
        _ = [val(P, i, 0.0, 0) for P in params for i in P]
        N, fps = self.tl.nframes, self.tl.fps
        frames = range(N + 1) if env.dep else [0]
        if any(val(P, 3, 0.0, 0) > 1e-6 for P in params):
            self.note("approx", self.name, "Linear Wipe feather on a matte: hard edge")
        corners = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
        NV = 8                                     # fixed vertex count (a convex rect ∩ half-planes has ≤ 4 + cuts)

        def clip(poly, d, thr):
            out = []
            for i in range(len(poly)):
                a, b = poly[i], poly[(i + 1) % len(poly)]
                da, db = a[0] * d[0] + a[1] * d[1] - thr, b[0] * d[0] + b[1] * d[1] - thr
                if da >= 0:
                    out.append(a)
                if (da >= 0) != (db >= 0):
                    u = da / (da - db)
                    out.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
            return out
        series = []
        for f in frames:
            t = f / fps
            poly = list(corners)
            for P in params:
                c = max(0.0, min(1.0, val(P, 1, t, 0.0) / 100.0))
                ang = math.radians(val(P, 2, t, 90.0))
                d = (math.sin(ang), -math.cos(ang))
                pr = [x * d[0] + y * d[1] for x, y in corners]
                thr = min(pr) + c * (max(pr) - min(pr))
                if c >= 1.0 - 1e-9:
                    poly = []
                elif c > 1e-9:
                    poly = clip(poly, d, thr)
            if not poly:
                poly = [(w * 0.5, h * 0.5)]
            poly = poly[:NV] + [poly[-1]] * (NV - len(poly[:NV]))
            series.append(poly)
        rect = rects[0]
        parent = next(e for e in own.iter() if rect in e.children)
        idx = parent.children.index(rect)
        path = E("PointsPath", id=self.id("wipe", "path"), name="Linear Wipe", isClosed=True)
        for i in range(NV):
            vid = self.id("wipe", "v", i)
            v = E("StraightVertex", id=vid)
            xs, ys = [p[i][0] for p in series], [p[i][1] for p in series]
            v.set("x", float(xs[0]))
            v.set("y", float(ys[0]))
            path.add(v)
            for k, vals in (("x", xs), ("y", ys)):
                if len(vals) > 1 and max(vals) - min(vals) > 1e-6:
                    self.anim.put(vid, "StraightVertex", k, reduce_track(list(enumerate(vals))))
        parent.children[idx] = path
        self.note("converted", self.name, f"Linear Wipe on the matte solid → its geometry cut by the wipe ({'keyed per frame' if len(series) > 1 else 'static'})")

    @staticmethod
    def fx_params(fx):
        out = []
        try:
            for p in fx.properties:
                if type(p).__name__ == "Property":
                    out.append(p)
        except Exception:
            pass
        return out

    def effect_comment(self, fx):
        mn = getattr(fx, "match_name", "")
        parts = [f'ae: effect "{mn}"']
        for p in self.fx_params(fx):
            pm = getattr(p, "match_name", "")
            if not pm.startswith(mn + "-"):
                continue
            try:
                v = to_js(self.conv.engine.value(p, self.L, self.cb.comp, 0.0))
            except Exception:
                try:
                    v = to_js(p.value)
                except Exception:
                    continue
            slot = pm[len(mn) + 1:]
            if isinstance(v, bool):
                v = 1.0 if v else 0.0
            if isinstance(v, (int, float)):
                parts.append(f"{slot}={fmt(v)}")
            elif isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v) and max(v) <= 1.0001:
                parts.append(f"{slot}=#{argb(v)}")
            elif isinstance(v, list) and 2 <= len(v) <= 3 and all(isinstance(x, (int, float)) for x in v):
                parts.append(f"{slot}={','.join(fmt(x) for x in v)}")
        return " ".join(parts)

    def transform_effect(self, fx):
        """AE Transform effect (ADBE Geometry2) -> Node(position, rotation, scale, opacity) > Node(-anchor)"""
        P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in self.fx_params(fx)
             if getattr(p, "match_name", "").startswith("ADBE Geometry2-")}
        key = f"fx{getattr(fx, 'property_index', 0)}"
        node = E("Node", id=self.id(key), name=clean(fx.name))

        def ap(i, what):
            return self.prop(P.get(i), what) if P.get(i) is not None else None
        pos, anc = ap(2, "transform fx position"), ap(1, "transform fx anchor")
        ks = self.geo_point_scale(anc, pos)
        uni = P.get(11)
        uniform = bool(tonum(uni.value)) if uni is not None else True
        sh, sw = ap(3, "transform fx height"), ap(4, "transform fx width")
        rot, op = ap(7, "transform fx rotation"), ap(8, "transform fx opacity")
        if pos is not None:
            self.put(node, "x", pos, 0, ks)
            self.put(node, "y", pos, 1, ks)
        # uniform scale: AE uses -0003 ("Scale Height", shown as "Scale") for both axes and ignores the width
        if uniform:
            if sh is not None:
                self.put(node, "scaleX", sh, None, 0.01, default=1.0)
                self.put(node, "scaleY", sh, None, 0.01, default=1.0)
        else:
            if sh is not None:
                self.put(node, "scaleY", sh, None, 0.01, default=1.0)
            if sw is not None:
                self.put(node, "scaleX", sw, None, 0.01, default=1.0)
        if rot is not None:
            self.put(node, "rotation", rot, None, math.pi / 180)
        if op is not None:
            self.put(node, "opacity", op, None, 0.01, default=1.0)
        skew = P.get(5)
        if skew is not None and abs(tonum(skew.value)) > 1e-6:
            self.note("approx", clean(fx.name), "Transform effect skew is not in Rive: ignored")
        if anc is not None and (anc.animated or abs(tonum(anc.static(0))) > 1e-9 or abs(tonum(anc.static(1))) > 1e-9):
            inner = E("Node", id=self.id(key, "a"), name=clean(fx.name) + " · anchor")
            self.put(inner, "x", anc, 0, -ks)
            self.put(inner, "y", anc, 1, -ks)
            node.add(inner)
            self.note("converted", clean(fx.name), "Transform effect → nodes (exact on vector content)")
            return node, inner
        self.note("converted", clean(fx.name), "Transform effect → node (exact on vector content)")
        return node, node

    def apply_ramp(self, fx):
        """AE Gradient Ramp on a shape/text/solid layer -> a Rive gradient on each paint (points are comp coordinates,
        measured by rml2ae: they stay put when the layer moves, so a moving layer gets keyed gradient points)"""
        from .aexpr import Env
        mn = "ADBE Ramp"
        P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in self.fx_params(fx)
             if getattr(p, "match_name", "").startswith(mn + "-")}
        L, comp, eng = self.L, self.cb.comp, self.conv.engine
        env = Env(eng, None, L, comp, 0.0)

        def val(i, t, d):
            p = P.get(i)
            if p is None:
                return d
            try:
                return unwrap(eng.value(p, L, comp, t, parent_env=env))
            except Exception:
                return d
        N, fps = self.tl.nframes, self.tl.fps
        env.dep = False
        _ = [val(i, 0.0, 0) for i in P]
        eng.layer_matrix(L, comp, env, 0.0)
        frames = range(N + 1) if env.dep else [0]
        radial = int(tonum(val(5, 0.0, 1))) == 2
        if tonum(val(6, 0.0, 0)) > 1e-6:
            self.note("approx", clean(fx.name), "gradient ramp scatter (noise) is not reproduced")
        found = []

        def walk(e, m):
            if e.tag in ("Node", "Shape", "Text"):
                m = geom.mul(m, geom.trs((tonum(e.attrs.get("x", 0)), tonum(e.attrs.get("y", 0))),
                                         math.degrees(tonum(e.attrs.get("rotation", 0))),
                                         (tonum(e.attrs.get("scaleX", 1)), tonum(e.attrs.get("scaleY", 1))), (0.0, 0.0)))
            if e.tag in ("Fill", "Stroke"):
                found.append((e, m))
                return
            for c in e.children:
                walk(c, m)
        for d in self.drawables:
            walk(d, geom.IDENT)
        if not found:
            self.note("approx", clean(fx.name), "gradient ramp on a layer without paints: ignored")
            return
        per_frame = []
        for f in frames:
            t = f / fps
            inv_l = geom.invert(eng.layer_matrix(L, comp, env, t)) or geom.IDENT
            s, e_ = val(1, t, [0, 0]), val(3, t, [100, 0])
            per_frame.append((f, geom.apply(inv_l, (tonum(s[0]), tonum(s[1]))), geom.apply(inv_l, (tonum(e_[0]), tonum(e_[1]))),
                              val(2, t, [0, 0, 0, 1]), val(4, t, [1, 1, 1, 1]), tonum(val(7, t, 0)) / 100))
        from .rml import prop_key
        for k, (paint, m) in enumerate(found):
            inv = geom.invert(m) or geom.IDENT
            old = [c for c in paint.children if c.tag in ("SolidColor", "LinearGradient", "RadialGradient")]
            orig_a, orig_rgb = 1.0, [1.0, 1.0, 1.0]
            for c in old:
                if c.tag == "SolidColor" and c.attrs.get("colorValue"):
                    orig_a, orig_rgb = _hex_argb(c.attrs["colorValue"])
            gone = {x.id for c in old for x in c.iter() if x.id}
            self.drop_tracks(gone)
            self.colors = [(kd, el) for kd, el in self.colors if el.id not in gone]
            paint.children = [c for c in paint.children if c not in old]
            g = E("RadialGradient" if radial else "LinearGradient", id=self.id(f"ramp{getattr(fx, 'property_index', 0)}", k),
                  name=clean(fx.name))
            s0 = E("GradientStop", id=self.id(f"ramp{getattr(fx, 'property_index', 0)}", k, "s0"), position=0.0)
            s1 = E("GradientStop", id=self.id(f"ramp{getattr(fx, 'property_index', 0)}", k, "s1"), position=1.0)
            g.add(s0)
            g.add(s1)
            paint.insert(0, g)
            dense = {"startX": [], "startY": [], "endX": [], "endY": []}
            cols = {s0: [], s1: []}
            for f, sp, ep, c0, c1, blend in per_frame:
                a, b = geom.apply(inv, sp), geom.apply(inv, ep)
                dense["startX"].append((f, a[0]))
                dense["startY"].append((f, a[1]))
                dense["endX"].append((f, b[0]))
                dense["endY"].append((f, b[1]))
                for st, c in ((s0, c0), (s1, c1)):
                    rgb = [tonum(c[i]) + (orig_rgb[i] - tonum(c[i])) * blend for i in range(3)]
                    cols[st].append((f, argb(rgb, orig_a)))
            for name, d in dense.items():
                self.put_dense(g, name, d)
            for st, d in cols.items():
                st.set("colorValue", d[0][1])
                keys, last = [], None
                for f, c in d:
                    if c != last:
                        keys.append((f, c, "linear", None))
                        last = c
                if len(keys) > 1:
                    self.anim.put(st.id, "GradientStop", "colorValue", keys, kind="color")
                self.colors.append(("stop", st))
        self.note("converted", clean(fx.name), f"Gradient Ramp → {'radial' if radial else 'linear'} gradient on "
                                                f"{len(found)} paint(s){' (keyed)' if len(frames) > 1 else ''}")

    def color_fn(self, fx):
        """colour effect -> f(rgb, t) (parameters read at t, expressions included); None if unsupported"""
        mn = fx.match_name
        P = {int(getattr(p, "match_name", "0-0").split("-")[-1]): p for p in self.fx_params(fx)
             if getattr(p, "match_name", "").startswith(mn + "-")}
        L, comp, eng = self.L, self.cb.comp, self.conv.engine

        def v(i, t, default=0.0):
            p = P.get(i)
            if p is None:
                return default
            try:
                return unwrap(eng.value(p, L, comp, t))
            except Exception:
                return default
        animated = any(self.prop(p, f"{clean(fx.name)} {clean(p.name)}").animated for p in P.values())
        self._fx_animated = getattr(self, "_fx_animated", False) or animated

        def lum(c):
            return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

        if mn == "ADBE Tint":
            def f(c, t):
                b, w, amt = v(1, t, [0, 0, 0, 1]), v(2, t, [1, 1, 1, 1]), tonum(v(3, t, 100)) / 100
                l = lum(c)
                m = [b[i] + (w[i] - b[i]) * l for i in range(3)]
                return [c[i] + (m[i] - c[i]) * amt for i in range(3)]
            return f
        if mn == "ADBE Fill":
            # ADBE Fill: -0001 fill mask, -0007 all masks, -0002 colour, -0006 invert, -0003/-0004 feathers,
            # -0005 opacity stored as a fraction (1 = 100 %). Reading -0007 as the opacity (0) left every colour
            # untouched (FX Monster's Cartoon_01 drew white instead of its customizer colours)
            col_i = next((i for i, p in sorted(P.items()) if isinstance(to_js(p.value), list) and len(to_js(p.value)) == 4), 2)
            op_i = 5 if 5 in P else None

            def f(c, t):
                col = v(col_i, t, [1, 0, 0, 1])
                o = max(0.0, min(1.0, tonum(v(op_i, t, 1.0)))) if op_i else 1.0
                return [c[i] + (col[i] - c[i]) * o for i in range(3)]
            return f
        if mn == "ADBE Invert":
            def f(c, t):
                ch = int(tonum(v(1, t, 1)))
                if ch not in (0, 1):
                    return c
                blend = tonum(v(2, t, 0)) / 100
                inv = [1 - x for x in c]
                return [inv[i] + (c[i] - inv[i]) * blend for i in range(3)]
            return f
        if mn == "ADBE Exposure2":
            def f(c, t):
                if int(tonum(v(1, t, 1))) != 1:
                    return c
                e, o, g = tonum(v(3, t, 0)), tonum(v(4, t, 0)), max(1e-3, tonum(v(5, t, 1)))
                out = []
                for x in c:
                    lin = x ** 2.2
                    y = max(0.0, lin * (2 ** e) + o)
                    y = y ** (1 / g)
                    out.append(min(1.0, y ** (1 / 2.2)))
                return out
            self.note("approx", clean(fx.name), "Exposure applied in a 2.2-gamma approximation of AE's linear light")
            return f
        if mn == "ADBE Easy Levels2":
            def f(c, t):
                if int(tonum(v(1, t, 1))) != 1:
                    return c
                ib, iw, g, ob, ow = tonum(v(3, t, 0)), tonum(v(4, t, 1)), max(1e-3, tonum(v(5, t, 1))), tonum(v(6, t, 0)), tonum(v(7, t, 1))
                out = []
                for x in c:
                    y = (x - ib) / (iw - ib) if iw != ib else 0.0
                    y = max(0.0, min(1.0, y)) ** (1 / g)
                    out.append(ob + (ow - ob) * y)
                return out
            return f
        if mn == "ADBE Color Balance (HLS)":
            def f(c, t):
                hue, light, sat = tonum(v(1, t, 0)), tonum(v(2, t, 0)) / 100, tonum(v(3, t, 0)) / 100
                h, l, s = colorsys.rgb_to_hls(*[max(0.0, min(1.0, x)) for x in c])
                h = (h + hue / 360.0) % 1.0
                l = l + (1 - l) * light if light > 0 else l * (1 + light)
                s = max(0.0, min(1.0, s * (1 + sat)))
                return list(colorsys.hls_to_rgb(h, l, s))
            return f
        if mn == "ADBE HUE SATURATION":
            # -0002 channel (1 = master), -0004/5/6 master hue (°) / saturation / lightness (±100), -0007 colorize,
            # -0008/9/10 its hue / saturation / lightness. FX packs recolour this way (broadcast-test: Bubble_29 → hue 331)
            def light(l, x):
                return l + (1 - l) * x if x > 0 else l * (1 + x)

            def f(c, t):
                c = [max(0.0, min(1.0, x)) for x in c]
                if tonum(v(7, t, 0)) >= 0.5:
                    l = light(lum(c), tonum(v(10, t, 0)) / 100)
                    return list(colorsys.hls_to_rgb((tonum(v(8, t, 0)) / 360.0) % 1.0, l,
                                                    max(0.0, min(1.0, tonum(v(9, t, 25)) / 100))))
                h, l, sat = colorsys.rgb_to_hls(*c)
                h = (h + tonum(v(4, t, 0)) / 360.0) % 1.0
                ds = tonum(v(5, t, 0)) / 100
                sat = max(0.0, min(1.0, sat * (1 + ds) if ds < 0 else sat + (1 - sat) * ds))
                l = light(l, tonum(v(6, t, 0)) / 100)
                return list(colorsys.hls_to_rgb(h, l, sat))
            self.note("approx", clean(fx.name), "Hue/Saturation applied in HSL (colorize: luminance re-tinted)")
            return f
        if mn == "ADBE Brightness & Contrast 2":
            def f(c, t):
                b, k = tonum(v(1, t, 0)) / 100, tonum(v(2, t, 0)) / 100
                return [max(0.0, min(1.0, (x - 0.5) * (1 + k) + 0.5 + b)) for x in c]
            self.note("approx", clean(fx.name), "Brightness & Contrast approximated (linear contrast around 50 %)")
            return f
        return None

    def apply_colors(self, fns):
        """bake colour effects into the layer's colours (static values and colour keys)"""
        animated = getattr(self, "_fx_animated", False)
        N, fps = self.tl.nframes, self.tl.fps

        def run(rgb, t):
            for _, f in fns:
                rgb = f(rgb, t)
            return [max(0.0, min(1.0, x)) for x in rgb]
        for kind, el in self.colors:
            attr = "colorValue"
            base = el.attrs.get(attr)
            if base is None:
                continue
            a, rgb = _hex_argb(base)
            tag = el.tag
            track = None
            from .rml import prop_key
            pk = prop_key(tag, attr)
            track = self.anim.tracks.get((el.id, pk))
            if track is None and not animated:
                el.set(attr, argb(run(rgb, 0.0), a))
                continue
            dense = []
            for f in range(N + 1):
                t = f / fps
                if track is not None:
                    a_t, rgb_t = _hex_argb(_color_at(track, f))
                else:
                    a_t, rgb_t = a, rgb
                dense.append((f, argb(run(rgb_t, t), a_t)))
            keys, last = [], None
            for f, c in dense:
                if c != last:
                    keys.append((f, c, "linear", None))
                    last = c
            el.set(attr, keys[0][1])
            if len(keys) > 1:
                self.anim.put(el.id, tag, attr, keys, kind="color")
            elif track is not None:
                self.drop_track(el.id, pk)
        if animated:
            self.note("converted", self.name, "animated colour effect baked into colour keys")

    def drop_track(self, oid, pk):
        k = (oid, pk)
        if k in self.anim.tracks:
            del self.anim.tracks[k]
            self.anim.order = [x for x in self.anim.order if x != k]

    def color_paint(self, sc, col, op, shape):
        """a shape paint's colour + opacity: static ARGB, colour keys, and/or the Shape's opacity keys"""
        alpha = 1.0
        op_anim = op is not None and op.p is not None and op.animated
        if op is not None and op.p is not None and not op_anim:
            alpha = tonum(op.static(None, 100)) / 100.0
        if col is None or col.p is None:
            sc.set("colorValue", argb([0, 0, 0], alpha))
        elif not col.animated:
            c = col.static(None)
            c = c if isinstance(c, list) else [0, 0, 0]
            sc.set("colorValue", argb(c, alpha))
        elif col.mode == "keys":
            eases = progress_keys(col.keys, self.tl.fps, self.stretch, col.p)
            keys = [(f, argb(to_js(k.value), alpha), interp, ease) for k, (f, interp, ease) in zip(col.keys, eases)]
            sc.set("colorValue", argb(to_js(col.at(0.0)), alpha))
            self.anim.put(sc.id, "SolidColor", "colorValue", keys, kind="color")
        else:
            keys, last = [], None
            for f, v in enumerate(col.frames):
                c = argb(unwrap(v), alpha)
                if c != last:
                    keys.append((f, c, "linear", None))
                    last = c
            sc.set("colorValue", keys[0][1])
            if len(keys) > 1:
                self.anim.put(sc.id, "SolidColor", "colorValue", keys, kind="color")
        if op_anim:
            self.put(shape, "opacity", op, None, 0.01, default=1.0)
        self.colors.append(("solid", sc))

    # ---------------------------------------------------------------- paths
    def points_path(self, key, name, src, m=geom.IDENT, round_prop=None):
        samples = src.samples()
        if not samples:
            return None
        if not geom.is_identity(m):
            samples = [geom.transform_path(s, m) for s in samples]
        n = max(len(s[0]) for s in samples)
        if n == 0:
            return None
        samples = [geom.resample(s, n) if len(s[0]) < n else s for s in samples]
        if any(len(s[0]) != n for s in samples):
            # (a one-vertex key cannot be resampled: FX Monster's Cartoon_01 masks) keep the fullest shape
            self.note("approx", name, "path vertex count changes over time: static shape")
            samples = [max(samples, key=lambda s: len(s[0]))]
            n = len(samples[0][0])
            src = PathSource()
            src.static = samples[0]
        el = E("PointsPath", id=self.id(key, "pp"), name=name, isClosed=bool(samples[0][3]))
        cubic = []
        for i in range(n):
            c = any(abs(s[1][i][0]) > 1e-6 or abs(s[1][i][1]) > 1e-6 or abs(s[2][i][0]) > 1e-6 or abs(s[2][i][1]) > 1e-6
                    for s in samples)
            cubic.append(c)
        # per vertex, per component: values over the samples (angles unwrapped). A zero handle has no angle: it takes
        # its neighbour's (the previous one, or the next for leading zeros — a shape keyed from size 0 would otherwise
        # interpolate its handles' angles from 0 and bulge into a diamond)
        def angles(vecs):
            raw = [geom.polar(v) for v in vecs]
            ang = [a if d >= 1e-6 else None for a, d in raw]
            first = next((a for a in ang if a is not None), 0.0)
            out, prev = [], None
            for a in ang:
                a = (prev if prev is not None else first) if a is None else a
                a = geom.unwrap(prev, a)
                out.append(a)
                prev = a
            return out, [d for _, d in raw]

        comps = []
        for i in range(n):
            series = {"x": [s[0][i][0] for s in samples], "y": [s[0][i][1] for s in samples]}
            if cubic[i]:
                series["inRotation"], series["inDistance"] = angles([s[1][i] for s in samples])
                series["outRotation"], series["outDistance"] = angles([s[2][i] for s in samples])
            comps.append(series)
        for i in range(n):
            vid = self.id(key, "v", i)
            tag = "CubicDetachedVertex" if cubic[i] else "StraightVertex"
            v = E(tag, id=vid)
            for k, vals in comps[i].items():
                v.set(k, float(vals[0]))
            el.add(v)
            if src.keys is not None:
                for k, vals in comps[i].items():
                    if max(vals) - min(vals) < 1e-6:
                        continue
                    keys = [(f, val, interp, ease) for (f, interp, ease), val in zip(src.eases, vals)]
                    self.anim.put(vid, tag, k, keys)
            elif src.frames is not None:
                for k, vals in comps[i].items():
                    if max(vals) - min(vals) < 1e-6:
                        continue
                    self.anim.put(vid, tag, k, reduce_track(list(enumerate(vals))))
            if round_prop is not None and not cubic[i]:
                rp = self.prop(round_prop.sub("ADBE Vector RoundCorner Radius"), "round corners")
                self.put(v, "radius", rp, None)
        if round_prop is not None and any(cubic):
            self.note("approx", name, "Round Corners only rounds straight corners in Rive (curved vertices untouched)")
        return el


def _c(v, dim):
    v = unwrap(v)
    if dim is None:
        return tonum(v[0]) if isinstance(v, list) else tonum(v)
    return tonum(v[dim]) if isinstance(v, list) else tonum(v)


def _hex_argb(h):
    h = h if isinstance(h, str) else argb(h)
    a = int(h[0:2], 16) / 255
    return a, [int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, int(h[6:8], 16) / 255]


def _color_at(track, f):
    keys = sorted(track.keys, key=lambda k: k[0])
    prev = keys[0]
    for k in keys:
        if k[0] <= f:
            prev = k
        else:
            if prev[2] == "hold" or k[0] == prev[0]:
                return prev[1]
            u = (f - prev[0]) / (k[0] - prev[0])
            if prev[2] == "cubic" and prev[3]:
                from .aexpr import _bez
                u = _bez(u, *prev[3])
            a0, c0 = _hex_argb(prev[1])
            a1, c1 = _hex_argb(k[1])
            return argb([c0[i] + (c1[i] - c0[i]) * u for i in range(3)], a0 + (a1 - a0) * u)
    return prev[1]
