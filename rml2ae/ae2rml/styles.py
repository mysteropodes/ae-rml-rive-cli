"""After Effects Layer Styles (Layer > Layer Styles) on vector layers -> Rive paints.

Layer Styles are not effects: py-aep lists them under 'ADBE Layer Styles', one group per style ('dropShadow/enabled',
'outerGlow/enabled'…) whose parameters are named '<style>/<param>' (Photoshop's names). Each style becomes a copy of
the layer's drawables with its paints replaced, drawn above or below the layer in Photoshop's stacking order (top to
bottom: Stroke, Inner Shadow, Inner Glow, Satin, Color Overlay, Gradient Overlay, Pattern Overlay, the layer, Outer
Glow, Drop Shadow).

  Drop Shadow   copy below, offset along the light angle, feathered (Size), Spread grows it with a stroke
  Outer Glow    copy below, feathered, screen by default
  Inner Shadow  copy on top, Rive inner Feather (inner = true) with the shadow offset
  Inner Glow    copy on top, inner Feather without offset (Source: Edge)
  Color Overlay copy on top in the style colour
  Gradient Ov.  copy on top, a Rive gradient across the layer's bounds (Linear, Radial; others approximated)
  Stroke        copy on top, every fill turned into a Stroke at the style's Position (Rive Stroke.position)
  Bevel and Emboss, Satin, Pattern Overlay: reported, not converted.

Not measured against After Effects yet: the Size -> Feather factor (STYLE_SIZE_K) is Photoshop's rule of thumb (the
blur reaches about 3 σ; Rive's Feather strength is 2 σ). Animated style parameters use their first value.
"""
import math

from . import geom
from .aexpr import tonum, unwrap
from .rml import E
from .util import argb, clean

# Layer Styles blend menu (py-aep psd_styles._BLEND_MODE2) -> Rive blend mode
STYLE_BLEND = {1: "srcOver", 4: "darken", 5: "multiply", 6: "colorBurn", 10: "lighten", 11: "screen",
               12: "colorDodge", 13: "additive", 16: "overlay", 17: "softLight", 18: "hardLight", 24: "difference",
               25: "exclusion", 27: "hue", 28: "saturation", 29: "color", 30: "luminosity"}
STYLE_BLEND_NEAREST = {2: "srcOver", 7: "colorBurn", 8: "darken", 14: "lighten", 19: "colorDodge", 20: "overlay",
                       21: "overlay", 22: "hardLight", 32: "difference", 33: "colorDodge"}
# frameFX/style (1 Outside, 2 Inside, 3 Center) -> rive StrokePosition (0 inside, 1 center, 2 outside)
STROKE_POSITION = {1: 2, 2: 0, 3: 1}
ABOVE = ("frameFX", "innerShadow", "innerGlow", "solidFill", "gradientFill")      # top first
BELOW = ("outerGlow", "dropShadow")                                                   # top first
NOT_CONVERTED = {"bevelEmboss": "Bevel and Emboss", "chromeFX": "Satin", "patternFill": "Pattern Overlay"}
PAINTS = ("SolidColor", "LinearGradient", "RadialGradient", "Feather")


class LayerStyles:
    """LayerBuild mixin"""
    STYLE_SIZE_K = 0.66

    def style_groups(self):
        """enabled Layer Styles of the layer: [(prefix, group)]"""
        try:
            ls = self.L.property("ADBE Layer Styles")
        except Exception:
            ls = None
        if ls is None or not getattr(ls, "enabled", False):
            return []
        from .aexpr import _children
        return [(g.match_name.split("/")[0], g) for g in _children(ls)
                if getattr(g, "enabled", False) and "/" in getattr(g, "match_name", "")]

    def style_value(self, g, prefix, name, default):
        q = None
        try:
            q = g.property(f"{prefix}/{name}")
        except Exception:
            pass
        if q is None:
            return default
        try:
            ap = self.prop(q, f"{prefix} {name}")
            if ap.animated:
                self.note("approx", clean(g.name), f"animated layer style parameter '{clean(q.name)}': its first value is used")
                return unwrap(ap.at(0.0))
            v = ap.static(None, default)
        except Exception:
            v = getattr(q, "value", default)
        return default if v is None else v

    def style_angle(self, g, prefix):
        """light angle in degrees: the style's own, or the layer's Global Light when 'Use Global Light' is on"""
        if int(tonum(self.style_value(g, prefix, "useGlobalAngle", 0))):
            try:
                bo = self.L.property("ADBE Layer Styles").property("ADBE Blend Options Group")
                return tonum(bo.property("ADBE Global Angle2").value)
            except Exception:
                return 120.0
        return tonum(self.style_value(g, prefix, "localLightingAngle", 120.0))

    def style_blend(self, g, prefix):
        mode = int(tonum(self.style_value(g, prefix, "mode2", 1)))
        if mode in STYLE_BLEND:
            return STYLE_BLEND[mode]
        near = STYLE_BLEND_NEAREST.get(mode, "srcOver")
        self.note("approx", clean(g.name), f"style blend mode {mode} is not in Rive: {near} (nearest)")
        return near

    def layer_styles(self, groups, holder):
        """the enabled styles -> copies of the drawables in `holder` (first child = drawn on top)"""
        srcs = [d for d in self.drawables if d.tag not in ("Image", "NestedArtboard", "Solo")]
        if not srcs or len(srcs) != len(self.drawables):
            self.note("unsupported", self.name, "layer style(s) on pixels (footage, precomp) not converted: "
                                                + ", ".join(clean(g.name) for _, g in groups))
            return
        by = dict(groups)
        for prefix, label in NOT_CONVERTED.items():
            if prefix in by:
                self.note("unsupported", clean(by[prefix].name), f"layer style {label} is not converted")
        first = next((i for i, x in enumerate(holder.children) if x in srcs), 0)
        above = [w for w in (self._style(p, by[p], srcs) for p in ABOVE if p in by) if w is not None]
        below = [w for w in (self._style(p, by[p], srcs) for p in BELOW if p in by) if w is not None]
        holder.children[first:first] = above
        holder.children.extend(below)
        if self.L.property("ADBE Layer Styles") is not None:
            try:
                fo = tonum(self.L.property("ADBE Layer Styles").property("ADBE Blend Options Group")
                           .property("ADBE Adv Blend Group").property("ADBE Layer Fill Opacity2").value)
                if fo < 100 - 1e-6:
                    self.note("approx", self.name, f"layer style Fill Opacity {fo:g} %: the layer is drawn at 100 %")
            except Exception:
                pass

    # ---------------------------------------------------------------- one style
    def _style(self, prefix, g, srcs):
        fn = {"dropShadow": self._drop_shadow, "outerGlow": self._outer_glow, "innerShadow": self._inner,
              "innerGlow": self._inner, "solidFill": self._color_overlay, "gradientFill": self._gradient_overlay,
              "frameFX": self._stroke_style}[prefix]
        if tonum(self.style_value(g, prefix, "noise", 0)) > 1e-6:
            self.note("approx", clean(g.name), "layer style noise is not reproduced")
        return fn(prefix, g, srcs)

    def _copy(self, srcs, key, label, paint, blend):
        """copy of the drawables, each paint emptied then refilled by paint(e) (-> False to drop it)"""
        wrap = E("Node", id=self.id("style", key), name=f"{self.name} · {label}")
        removed = set()
        for d in srcs:
            c = self.clone(d, f"style{key}")
            c.name = f"{d.name} · {label}"
            for e in list(c.iter()):
                if e.tag in ("Shape", "Text") and blend not in (None, "srcOver"):
                    e.set("blendModeValue", blend)
                kept = []
                for x in e.children:
                    if x.tag in ("Fill", "Stroke"):
                        for y in list(x.children):
                            if y.tag in PAINTS:
                                removed.update(z.id for z in y.iter() if z.id)
                                x.children.remove(y)
                        if paint(x) is False:
                            removed.update(z.id for z in x.iter() if z.id)
                            continue
                    kept.append(x)
                e.children = kept
            wrap.add(c)
        self.drop_tracks(removed)
        return wrap

    def _feather(self, size, **kw):
        return E("Feather", id=self.conv.ids.new(), name="Size", strength=max(0.01, self.STYLE_SIZE_K * size), **kw)

    def _color(self, g, prefix, default):
        col = self.style_value(g, prefix, "color", default)
        col = list(col) if isinstance(col, (list, tuple)) else list(default)
        a = (col[3] if len(col) > 3 else 1.0) * tonum(self.style_value(g, prefix, "opacity", 75.0)) / 100.0
        return argb(col[:3], a)

    def _outer(self, prefix, g, srcs, label, color, dx, dy):
        size = tonum(self.style_value(g, prefix, "blur", 5.0))
        spread = max(0.0, min(100.0, tonum(self.style_value(g, prefix, "chokeMatte", 0.0)))) / 100.0
        grow, blur = size * spread, size * (1 - spread)
        evenodd = []

        def paint(e):
            e.children.insert(0, E("SolidColor", id=self.conv.ids.new(), name=label, colorValue=color))
            if blur > 1e-6:
                e.add(self._feather(blur))
                if e.tag == "Fill":
                    evenodd.append(e.attrs.get("fillRule") == "evenOdd")
                    e.set("fillRule", "clockwise")    # Rive draws a feathered fill under the clockwise rule only
            if e.tag == "Stroke" and grow > 1e-6:
                e.set("thickness", tonum(e.attrs.get("thickness", 1)) + 2 * grow)

        wrap = self._copy(srcs, prefix, label.lower(), paint, self.style_blend(g, prefix))
        if grow > 1e-6:
            # Spread: the copy grows by `grow` before the blur -> a centred stroke of twice that width on each fill
            for e in [x for x in wrap.iter() if x.tag in ("Shape", "TextStylePaint")]:
                for f in [x for x in e.children if x.tag == "Fill"]:
                    s = E("Stroke", id=self.conv.ids.new(), name="Spread", thickness=2 * grow, join="round")
                    s.add(E("SolidColor", id=self.conv.ids.new(), name=label, colorValue=color))
                    if blur > 1e-6:
                        s.add(self._feather(blur))
                    e.children.insert(e.children.index(f), s)
        if dx or dy:
            wrap.set("x", dx)
            wrap.set("y", dy)
        if any(evenodd):
            self.note("approx", clean(g.name), "even-odd shapes: their feathered style copy fills by the clockwise rule")
        return wrap, size, spread

    def _drop_shadow(self, prefix, g, srcs):
        ang = math.radians(self.style_angle(g, prefix))
        dist = tonum(self.style_value(g, prefix, "distance", 5.0))
        # the light comes from `ang` (counter-clockwise from +x, y up): the shadow falls the other way
        dx, dy = round(-dist * math.cos(ang), 6) + 0.0, round(dist * math.sin(ang), 6) + 0.0
        wrap, size, spread = self._outer(prefix, g, srcs, "Shadow", self._color(g, prefix, [0, 0, 0, 1]), dx, dy)
        self.note("approx", clean(g.name), f"Drop Shadow style → offset copy below ({dx:.3g}, {dy:.3g}), size {size:g} → "
                                           f"feather {self.STYLE_SIZE_K * size * (1 - spread):.3g} (not measured in AE)")
        return wrap

    def _outer_glow(self, prefix, g, srcs):
        if int(tonum(self.style_value(g, prefix, "AEColorChoice", 1))) != 1:
            self.note("approx", clean(g.name), "gradient Outer Glow: its first colour is used")
            col = self._gradient_first(g, prefix)
        else:
            col = self._color(g, prefix, [1, 1, 0.745, 1])
        wrap, size, _ = self._outer(prefix, g, srcs, "Glow", col, 0.0, 0.0)
        self.note("approx", clean(g.name), f"Outer Glow style → feathered copy below (size {size:g}; not measured in AE)")
        return wrap

    def _gradient_first(self, g, prefix):
        from .shapes import gradient_stops
        stops = gradient_stops(self.style_value(g, prefix, "gradient", None))
        c = stops[0][1]
        return argb(c[:3], c[3] * tonum(self.style_value(g, prefix, "opacity", 75.0)) / 100.0)

    def _inner(self, prefix, g, srcs):
        shadow = prefix == "innerShadow"
        size = tonum(self.style_value(g, prefix, "blur", 5.0))
        choke = max(0.0, min(100.0, tonum(self.style_value(g, prefix, "chokeMatte", 0.0)))) / 100.0
        if shadow:
            col = self._color(g, prefix, [0, 0, 0, 1])
            ang = math.radians(self.style_angle(g, prefix))
            dist = tonum(self.style_value(g, prefix, "distance", 5.0))
            ox, oy = round(-dist * math.cos(ang), 6) + 0.0, round(dist * math.sin(ang), 6) + 0.0
        else:
            if int(tonum(self.style_value(g, prefix, "AEColorChoice", 1))) != 1:
                self.note("approx", clean(g.name), "gradient Inner Glow: its first colour is used")
                col = self._gradient_first(g, prefix)
            else:
                col = self._color(g, prefix, [1, 1, 0.745, 1])
            ox = oy = 0.0
            if int(tonum(self.style_value(g, prefix, "innerGlowSource", 1))) == 2:
                self.note("approx", clean(g.name), "Inner Glow from the centre: drawn from the edge")
        if choke > 1e-6:
            self.note("approx", clean(g.name), f"inner style choke {choke * 100:g} % is not reproduced")
        strokes = []

        def paint(e):
            if e.tag != "Fill":
                strokes.append(e)
                return False                      # Rive feathers a stroke outward only: no inside to shade
            e.children.insert(0, E("SolidColor", id=self.conv.ids.new(), name="Shadow" if shadow else "Glow",
                                   colorValue=col))
            e.add(self._feather(size, offsetX=ox or None, offsetY=oy or None, inner=True))
            e.set("fillRule", "clockwise")
        label = "inner shadow" if shadow else "inner glow"
        wrap = self._copy(srcs, prefix, label, paint, self.style_blend(g, prefix))
        if strokes:
            self.note("approx", clean(g.name), f"{label} style: stroked parts of the layer are not shaded")
        self.note("approx", clean(g.name), f"{'Inner Shadow' if shadow else 'Inner Glow'} style → Rive inner feather "
                                           f"(size {size:g}{f', offset {ox:.3g}, {oy:.3g}' if shadow else ''}; not measured in AE)")
        return wrap

    def _color_overlay(self, prefix, g, srcs):
        col = self._color(g, prefix, [1, 0, 0, 1])

        def paint(e):
            e.children.insert(0, E("SolidColor", id=self.conv.ids.new(), name="Overlay", colorValue=col))
        self.note("converted", clean(g.name), "Color Overlay style → a copy on top in its colour")
        return self._copy(srcs, prefix, "color overlay", paint, self.style_blend(g, prefix))

    def _stroke_style(self, prefix, g, srcs):
        col = self._color(g, prefix, [1, 0, 0, 1])
        size = tonum(self.style_value(g, prefix, "size", 3.0))
        pos = int(tonum(self.style_value(g, prefix, "style", 1)))
        rpos = STROKE_POSITION.get(pos, 2)
        dropped = []

        def paint(e):
            if e.tag == "Stroke":
                dropped.append(e)
                return False
            e.tag = "Stroke"                      # same paint, outlined: keeps its trims / path effects
            for k in ("fillRule",):
                e.attrs.pop(k, None)
            e.set("thickness", size)
            e.set("join", "round")
            e.set("position", rpos)
            e.name = "Stroke"
            e.children.insert(0, E("SolidColor", id=self.conv.ids.new(), name="Stroke", colorValue=col))
        wrap = self._copy(srcs, prefix, "stroke", paint, self.style_blend(g, prefix))
        if dropped:
            self.note("approx", clean(g.name), "Stroke style follows the layer's fills only, not its strokes")
        self.note("converted", clean(g.name), f"Stroke style → {size:g} px stroke "
                                              f"{({0: 'inside', 1: 'centred on', 2: 'outside'})[rpos]} the shapes")
        return wrap

    # ---------------------------------------------------------------- gradient overlay
    def style_bounds(self):
        """the layer's content box in layer space at time 0, or None"""
        from .aexpr import Env
        eng, comp, L = self.conv.engine, self.cb.comp, self.L
        try:
            r = eng.source_rect(L, comp, Env(eng, None, L, comp, 0.0), 0.0, False)
            x0, y0 = tonum(r["left"]), tonum(r["top"])
            return x0, y0, x0 + tonum(r["width"]), y0 + tonum(r["height"])
        except Exception:
            return None

    def _gradient_overlay(self, prefix, g, srcs):
        from .shapes import gradient_stops
        box = self.style_bounds()
        if box is None or box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
            self.note("approx", clean(g.name), "Gradient Overlay style: the layer has no bounds, not converted")
            return None
        op = tonum(self.style_value(g, prefix, "opacity", 100.0)) / 100.0
        stops = gradient_stops(self.style_value(g, prefix, "gradient", None))
        kind = int(tonum(self.style_value(g, prefix, "type", 1)))
        if int(tonum(self.style_value(g, prefix, "reverse", 0))):
            stops = [(1 - p, c) for p, c in reversed(stops)]
        if kind == 4:                                   # Reflected: mirrored around the centre
            stops = [(0.5 - p / 2, c) for p, c in reversed(stops)] + [(0.5 + p / 2, c) for p, c in stops]
        if kind in (3, 5):
            self.note("approx", clean(g.name), f"Gradient Overlay style {'Angle' if kind == 3 else 'Diamond'}: drawn linear")
        if not int(tonum(self.style_value(g, prefix, "align", 1))):
            self.note("approx", clean(g.name), "Gradient Overlay not aligned with the layer: aligned with it")
        a = math.radians(tonum(self.style_value(g, prefix, "angle", 90.0)))
        sc = tonum(self.style_value(g, prefix, "scale", 100.0)) / 100.0
        off = self.style_value(g, prefix, "offset", [0, 0])
        off = list(off) if isinstance(off, (list, tuple)) else [0, 0]
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        cx, cy = (x0 + x1) / 2 + tonum(off[0]), (y0 + y1) / 2 + tonum(off[1])
        radial = kind == 2
        if radial:
            r = math.hypot(w, h) / 2 * sc
            p0, p1 = (cx, cy), (cx + r, cy)
        else:
            half = (abs(w * math.cos(a)) + abs(h * math.sin(a))) / 2 * sc
            ux, uy = math.cos(a), -math.sin(a)                       # 90° runs bottom -> top
            p0, p1 = (cx - ux * half, cy - uy * half), (cx + ux * half, cy + uy * half)
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

        wrap = self._copy(srcs, prefix, "gradient overlay", lambda e: None, self.style_blend(g, prefix))
        for c in wrap.children:
            walk(c, geom.IDENT)
        for e, m in found:
            inv = geom.invert(m) or geom.IDENT
            (sx, sy), (ex, ey) = geom.apply(inv, p0), geom.apply(inv, p1)
            gr = e.insert(0, E("RadialGradient" if radial else "LinearGradient", id=self.conv.ids.new(),
                               name="Gradient Overlay", startX=sx, startY=sy, endX=ex, endY=ey))
            for p, col in stops:
                gr.add(E("GradientStop", id=self.conv.ids.new(), position=float(max(0.0, min(1.0, p))),
                         colorValue=argb(col[:3], col[3] * op)))
        self.note("converted", clean(g.name), f"Gradient Overlay style → a {'radial' if radial else 'linear'} gradient "
                                              f"across the layer's bounds on a copy on top")
        return wrap
