"""AE effects declared in the RML as comments — the bridge for what Rive cannot draw (shadows, blurs…).

Syntax, on the line(s) right before the element (Shape, Text, Image, Node…) that receives the effect:

    <!-- ae: DropShadow x=0 y=4 blur=12 color=#000000 opacity=0.25 -->
    <!-- ae: GaussianBlur radius=8 -->
    <!-- ae: effect "ADBE Gaussian Blur 2" 0001=20 0002=1 -->          # raw: any AE matchName + parameter slots

Several comments stack (AE applies them in order). Rive ignores comments; `rive --verify` accepts them (measured).
figma2rml writes them from Figma effects; a designer or an agent can write them by hand.

Each named effect is a function (params dict) -> [(matchName, {paramMatchName: value})]. Values: numbers, [r, g, b]
in 0..1, booleans as 1/0. AE units below were checked against the AE 2026 scripting API (Drop Shadow opacity is
0..255, direction in degrees clockwise from up, softness in px).
"""
import math
import re


def _color(s):
    s = s.lstrip("#")
    if len(s) == 8:          # AARRGGBB (Rive) or RRGGBBAA? Rive writes AARRGGBB in RML: keep that convention
        a, s = int(s[:2], 16) / 255, s[2:]
    return [int(s[0:2], 16) / 255, int(s[2:4], 16) / 255, int(s[4:6], 16) / 255]


def _alpha(s):
    s = s.lstrip("#")
    return int(s[:2], 16) / 255 if len(s) == 8 else 1.0


def drop_shadow(p):
    x, y = float(p.get("x", 0)), float(p.get("y", 4))
    blur = float(p.get("blur", 10))
    col = p.get("color", "#000000")
    op = float(p.get("opacity", _alpha(col)))
    direction = (math.degrees(math.atan2(x, -y)) + 360) % 360          # AE: 0 = up, 90 = right, 135 = down-right
    return [("ADBE Drop Shadow", {"ADBE Drop Shadow-0001": _color(col), "ADBE Drop Shadow-0002": op * 255,
                                  "ADBE Drop Shadow-0003": direction, "ADBE Drop Shadow-0004": math.hypot(x, y),
                                  "ADBE Drop Shadow-0005": blur})]


def gaussian_blur(p):
    # AE blurriness ≈ Figma blur radius (a 94 px Figma layer blur at x2 vanished a 200 px band in AE — measured)
    return [("ADBE Gaussian Blur 2", {"ADBE Gaussian Blur 2-0001": float(p.get("radius", 10)), "ADBE Gaussian Blur 2-0002": 1, "ADBE Gaussian Blur 2-0003": 1})]


def inner_shadow(p):
    # no native inner-shadow effect and AE layer styles cannot be added by script: reported, not applied
    # (a first attempt with Drop Shadow "shadow only" + Set Matte blanked the layer — measured)
    return [("?", {"name": "InnerShadow (no AE effect; add the Inner Shadow layer style by hand)"})]


def glow(p):
    col = p.get("color", "#FFFFFF")
    return [("ADBE Glo2", {"ADBE Glo2-0002": float(p.get("threshold", 60)), "ADBE Glo2-0003": float(p.get("radius", 20)),
                           "ADBE Glo2-0004": float(p.get("intensity", 1.0)), "ADBE Glo2-0007": 2,
                           "ADBE Glo2-0009": _color(col), "ADBE Glo2-0010": _color(col)})]


def tint(p):
    return [("ADBE Tint", {"ADBE Tint-0001": _color(p.get("black", "#000000")), "ADBE Tint-0002": _color(p.get("white", "#FFFFFF")),
                           "ADBE Tint-0003": float(p.get("amount", 100))})]


NAMED = {"DropShadow": drop_shadow, "GaussianBlur": gaussian_blur, "Blur": gaussian_blur, "InnerShadow": inner_shadow,
         "Glow": glow, "Tint": tint}

_TOK = re.compile(r'(\w+)=("([^"]*)"|\S+)|"([^"]*)"|(\S+)')


def parse(comment):
    """'ae: Name k=v …' or 'ae: effect "match" 0001=v …' -> [(matchName, {param: value})] ; None if not an ae: comment"""
    text = comment.strip()
    if not text.lower().startswith("ae:"):
        return None
    text = text[3:].strip()
    if not text:
        return None
    parts = text.split(None, 1)
    name, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    params, positional = {}, []
    for m in _TOK.finditer(rest):
        if m.group(1):
            params[m.group(1)] = m.group(3) if m.group(3) is not None else m.group(2)
        else:
            positional.append(m.group(4) if m.group(4) is not None else m.group(5))
    if name.lower() == "effect":
        if not positional:
            return []
        match = positional[0]
        vals = {}
        for k, v in params.items():
            key = k if k.startswith("ADBE") or "-" in k else f"{match}-{k}"
            vals[key] = _value(v)
        return [(match, vals)]
    fn = NAMED.get(name)
    if fn is None:
        return [("?", {"name": name})]
    return fn(params)


def _value(v):
    if isinstance(v, str) and v.startswith("#"):
        return _color(v)
    if isinstance(v, str) and "," in v:                  # a point "x,y" (written by ae2rml for point parameters)
        try:
            return [float(x) for x in v.split(",")]
        except ValueError:
            return v
    try:
        return float(v)
    except ValueError:
        return v


def merge_shadows(shadows):
    """Figma 'smooth shadow' presets stack 3-5 drop shadows; stacked AE Drop Shadow effects compound (each one
    shadows the previous result, measured: much heavier) -> one equivalent shadow: opacity-weighted offset and blur,
    combined opacity 1 - prod(1 - a)"""
    if len(shadows) <= 1:
        return shadows
    ws = [max(s["color"].get("a", 1), 1e-6) for s in shadows]
    tot = sum(ws)
    x = sum(s["offset"]["x"] * w for s, w in zip(shadows, ws)) / tot
    y = sum(s["offset"]["y"] * w for s, w in zip(shadows, ws)) / tot
    r = sum(s.get("radius", 0) * w for s, w in zip(shadows, ws)) / tot
    a = 1.0
    for w in ws:
        a *= (1 - w)
    c = dict(shadows[0]["color"])
    c["a"] = 1 - a
    return [{"type": "DROP_SHADOW", "offset": {"x": x, "y": y}, "radius": r, "color": c, "visible": True}]


def figma_effects(effects):
    """Figma effect dicts -> ae: comment strings (what figma2rml writes)"""
    out = []
    effects = [e for e in effects or [] if e.get("visible", True)]
    drops = [e for e in effects if e.get("type") == "DROP_SHADOW" and e.get("color", {}).get("a", 1) > 0.002]
    if len(drops) > 1:
        effects = [e for e in effects if e.get("type") != "DROP_SHADOW"] + merge_shadows(drops)
    for e in effects:
        if not e.get("visible", True):
            continue
        t = e.get("type")
        c = e.get("color", {"r": 0, "g": 0, "b": 0, "a": 0.25})
        if t in ("DROP_SHADOW", "INNER_SHADOW") and c.get("a", 1) <= 0.002:
            continue                                            # Figma "smooth shadow" presets end with an invisible layer
        alpha = c.get("a", 1)
        if t == "DROP_SHADOW":
            alpha *= 0.8          # AE's Drop Shadow reads ~20 % darker than Figma's at equal alpha and softness (measured on text)
        col = "#%02X%02X%02X%02X" % (round(alpha * 255), round(c["r"] * 255), round(c["g"] * 255), round(c["b"] * 255))
        off = e.get("offset", {"x": 0, "y": 0})
        if t == "DROP_SHADOW":
            out.append(f"ae: DropShadow x={off['x']:g} y={off['y']:g} blur={e.get('radius', 0):g} color={col}" + (f" spread={e['spread']:g}" if e.get("spread") else ""))
        elif t == "INNER_SHADOW":
            out.append(f"ae: InnerShadow x={off['x']:g} y={off['y']:g} blur={e.get('radius', 0):g} color={col}")
        elif t == "LAYER_BLUR":
            if e.get("radius", 0) > 0.01:
                out.append(f"ae: GaussianBlur radius={e.get('radius', 0):g}")
        elif t == "BACKGROUND_BLUR":
            out.append(f"ae: BackgroundBlur radius={e.get('radius', 0):g}")     # unsupported in AE without an adjustment layer: reported
    return out
