"""RML (Rive CLI scene) -> a small object model, typed by the Rive schema (schema.json, from the rive-runtime
headers: tools/make_schema.py).

Nothing here knows about After Effects. `Project.load(dir)` reads rive.yaml + scene.rml (+ any *.rml next to it),
keeps every element (tag, attributes, children, id), and indexes: objects by id, artboards, assets, animations
(keys per (object id, property key)), state machines, view models.
"""
import json
import math
import os
import re
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = json.load(open(os.path.join(HERE, "schema.json"), encoding="utf-8"))
TYPES = SCHEMA["types"]
KEYS = {int(k): tuple(v) for k, v in SCHEMA["keys"].items()}       # property key -> (owner type, name, type)
_AMP = re.compile(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")

# Rive enum values as they can appear in RML (names) or as raw ints
BLEND_MODES = {3: "srcOver", 14: "screen", 15: "overlay", 16: "darken", 17: "lighten", 18: "colorDodge", 19: "colorBurn",
               20: "hardLight", 21: "softLight", 22: "difference", 23: "exclusion", 24: "multiply", 25: "hue",
               26: "saturation", 27: "color", 28: "luminosity"}


def ancestors(tag):
    """Type name -> [type, its parents...] from the schema ('' if unknown)."""
    t = TYPES.get(tag)
    return [tag] + (t["extends"] if t else [])


def is_a(tag, base):
    return base in ancestors(tag)


def prop_name(key):
    return KEYS.get(int(key), (None, f"key{key}", None))[1]


class El:
    """One RML element."""
    __slots__ = ("tag", "attrs", "children", "parent", "id", "ae")

    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []
        self.id = attrs.get("id")
        self.ae = []                # "ae: …" comments written right before this element (AE effects, see rml2ae/effects.py)

    def get(self, name, default=None):
        return self.attrs.get(name, default)

    def num(self, name, default=0.0):
        v = self.attrs.get(name)
        return float(v) if v not in (None, "") else float(default)

    def find(self, tag):
        for c in self.children:
            if c.tag == tag:
                return c
        return None

    def findall(self, tag):
        return [c for c in self.children if c.tag == tag]

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()

    @property
    def name(self):
        return self.attrs.get("name", self.tag)

    def __repr__(self):
        return f"<{self.tag} {self.name!r} id={self.id}>"


def argb(s, default=(0.0, 0.0, 0.0, 1.0)):
    """'AARRGGBB' (or '#RRGGBB') -> (r, g, b, a) in 0..1."""
    if not s:
        return default
    h = s.lstrip("#")
    if len(h) == 6:
        h = "FF" + h
    a, r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4, 6))
    return (r, g, b, a)


# LayerMask.maskModeValue (rive-runtime MaskMode) and its After Effects track matte type
MASK_MODES = {0: "alpha", 1: "invertedAlpha", 2: "luminance", 3: "invertedLuminance"}
MASK_MODE_NAMES = {v.lower(): k for k, v in MASK_MODES.items()}
MASK_MODE_NAMES.update({"luma": 2, "invertedluma": 3})


def layer_mask_flags(el):
    """LayerMask -> (mode 0-3, isVisible, sourceDraws). RML carries either the stored bit field maskFlags (mode in bits
    0-1, isVisible 1 << 2, sourceDraws 1 << 3; default 4) or the three properties it packs, by name or number"""
    raw = el.get("maskFlags")
    if raw not in (None, ""):
        f = int(float(raw))
        return f & 3, bool(f & 4), bool(f & 8)
    m = (el.get("maskModeValue") or "0").strip()
    mode = int(float(m)) if m.replace(".", "", 1).isdigit() else MASK_MODE_NAMES.get(m.lower(), 0)
    if mode not in MASK_MODES:
        mode = 0                      # the runtime reads an unknown mode as alpha
    return mode, el.get("isVisible", "true") != "false", el.get("sourceDraws", "false") == "true"


def blend_name(v):
    if v is None:
        return None
    if v.isdigit():
        return BLEND_MODES.get(int(v))
    return v


NUMERIC_INTERP = {"0": "hold", "1": "linear", "2": "cubic", "3": "cubicValue", "4": "elastic", "5": "scripted"}


def interp_of(kf):
    """interpolationType as the Rive runtime reads it (measured, CLI 1.3): ABSENT = hold (type 0), numeric enums as
    the editor exports them (0 hold, 1 linear, 2 cubic…), named values as written by hand"""
    raw = (kf.get("interpolationType") or "").strip()
    if not raw:
        return "hold"
    return NUMERIC_INTERP.get(raw, raw)


EASINGS = {"easein": 0, "easeout": 1, "easeinout": 2}


def _solve_x(x, x1, x2):
    """parameter t of a cubic bezier (0, x1, x2, 1) whose x is `x` (monotonic for x1, x2 in [0, 1])"""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        t = (lo + hi) / 2
        if 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3 < x:
            lo = t
        else:
            hi = t
    return (lo + hi) / 2


def elastic_progress(f, easing, amplitude, period):
    """ElasticInterpolator.transform (rive-runtime elastic_ease.cpp): progress of an elastic segment at f in [0, 1];
    easing 0 = in, 1 = out, 2 = in-out; a period of 0 is read as 0.5"""
    period = period or 0.5
    s = period / 4 if amplitude < 1 else period / (2 * math.pi) * math.asin(1 / amplitude)

    def amp(time):
        if amplitude < 1 and abs(time) < abs(s):
            lt = abs(time) / abs(s)
            return amplitude * lt + (1 - lt)
        return amplitude

    w = 2 * math.pi / period
    if easing == 0:
        t = f - 1
        return -(amp(t) * 2 ** (10 * t) * math.sin((-t - s) * w))
    if easing == 2:
        t = f * 2 - 1
        if t < 0:
            return -0.5 * amp(t) * 2 ** (10 * t) * math.sin((-t - s) * w)
        return 0.5 * (amp(t) * 2 ** (-10 * t) * math.sin((t - s) * w)) + 1
    if easing == 1:
        return amp(f) * 2 ** (-10 * f) * math.sin((f - s) * w) + 1
    return f


def segment_value(v0, v1, f, interp, ease):
    """value of a Rive key segment v0 -> v1 at progress f in [0, 1] (the interpolator of its FIRST key, as the runtime
    reads it): hold, linear, cubic (CubicEaseInterpolator), cubicValue (CubicValueInterpolator: y1 / y2 are values,
    not progress), elastic (ease = (easing, amplitude, period))"""
    if interp == "hold":
        return v0
    if interp == "cubic" and ease:
        t = _solve_x(f, ease[0], ease[2])
        p = 3 * (1 - t) ** 2 * t * ease[1] + 3 * (1 - t) * t * t * ease[3] + t ** 3
        return v0 + (v1 - v0) * p
    if interp == "cubicValue" and ease:
        t = _solve_x(f, ease[0], ease[2])
        return (1 - t) ** 3 * v0 + 3 * (1 - t) ** 2 * t * ease[1] + 3 * (1 - t) * t * t * ease[3] + t ** 3 * v1
    if interp == "elastic" and ease:
        return v0 + (v1 - v0) * elastic_progress(f, *ease)
    return v0 + (v1 - v0) * f


def bake_keys(lst):
    """Rive keys -> keys After Effects can carry: hold, linear and cubic kept; a CubicValueInterpolator between two
    different values is the AE bezier of the same handles (exact: its y1 / y2 normalised to the segment); an elastic
    segment, or a CubicValueInterpolator bump between equal values (no AE ease reaches a value outside its keys),
    becomes one linear key per frame on the runtime's curve (exact at every frame). `ae pull` folds them back."""
    out = []
    for i, (f, v, interp, ease) in enumerate(lst):
        if interp not in ("cubicValue", "elastic"):
            out.append((f, v, interp, ease))
            continue
        nxt = lst[i + 1] if i + 1 < len(lst) else None
        if nxt is None or not isinstance(v, (int, float)) or not isinstance(nxt[1], (int, float)):
            out.append((f, v, "linear", None))
            continue
        f1, v1 = nxt[0], nxt[1]
        if interp == "cubicValue" and abs(v1 - v) > 1e-9:
            dv = v1 - v
            out.append((f, v, "cubic", (ease[0], (ease[1] - v) / dv, ease[2], (ease[3] - v) / dv)))
            continue
        out.append((f, v, "linear", None))
        for g in range(int(f) + 1, int(math.ceil(f1))):
            if g > f:
                out.append((g, segment_value(v, v1, (g - f) / (f1 - f), interp, ease), "linear", None))
    return out


def interpolator_of(kf):
    """(interp, ease) from a non-hold key's interpolator child: None when it has none (linear), ("?", tag) when the
    interpolator is not one rml2ae reproduces. Omitted attributes are the runtime defaults (measured: an empty
    <CubicEaseInterpolator/> eases like 0.42, 0, 0.58, 1; ElasticInterpolator: out, amplitude 1, period 1)."""
    ci = kf.find("CubicEaseInterpolator")
    if ci is not None:
        return "cubic", (ci.num("x1", 0.42), ci.num("y1", 0.0), ci.num("x2", 0.58), ci.num("y2", 1.0))
    cv = kf.find("CubicValueInterpolator")
    if cv is not None:
        return "cubicValue", (cv.num("x1", 0.42), cv.num("y1", 0.0), cv.num("x2", 0.58), cv.num("y2", 1.0))
    el = kf.find("ElasticInterpolator")
    if el is not None:
        raw = (el.get("easingValue") or "1").strip()
        easing = EASINGS.get(raw.lower(), None) if not raw.isdigit() else int(raw)
        return "elastic", (1 if easing not in (0, 1, 2) else easing, el.num("amplitude", 1.0), el.num("period", 1.0))
    if kf.children:
        return "?", kf.children[0].tag
    return None


class Animation:
    """A LinearAnimation: keys[(object id, property key)] = [(frame, value, interp, ease)], After Effects-ready
    (bake_keys); raw_keys the same with the Rive interpolators as written (cubicValue, elastic)."""

    def __init__(self, el):
        self.el = el
        self.id = el.id
        self.name = el.name
        self.fps = el.num("fps", 60)
        self.duration = int(el.num("duration", 60))
        self.loop = el.get("loopValue", "loop")
        self.speed = el.num("speed", 1.0) or 1.0
        self.quantize = el.get("quantize") == "true"
        self.keys = {}
        self.unsupported = []
        for ko in el.findall("KeyedObject"):
            oid = ko.get("objectId")
            for kp in ko.findall("KeyedProperty"):
                pk = int(kp.get("propertyKey"))
                lst = self.keys.setdefault((oid, pk), [])
                for kf in kp.children:
                    frame = int(kf.num("frame", 0))
                    interp = interp_of(kf)
                    ease = None
                    if interp != "hold":
                        # the runtime: a non-hold key eases through its interpolator child, else it is linear
                        got = interpolator_of(kf)
                        if got is None:
                            interp = "linear"
                        elif got[0] == "?" or (got[0] != "cubic" and kf.tag != "KeyFrameDouble"):
                            self.unsupported.append((oid, pk, kf.children[0].tag))
                            interp = "linear"
                        else:
                            interp, ease = got
                    if kf.tag == "KeyFrameDouble":
                        value = kf.num("value")
                    elif kf.tag == "KeyFrameColor":
                        value = argb(kf.get("value"))
                        interp = "hold" if interp != "linear" else "linear"
                    elif kf.tag == "KeyFrameBool":
                        value = kf.get("value", "false") == "true"
                        interp = "hold"
                    elif kf.tag in ("KeyFrameId", "KeyFrameString", "KeyFrameInt", "KeyFrameUint"):
                        value = kf.get("value")
                        interp = "hold"
                    else:
                        value = kf.get("value")
                        interp = "hold"
                    lst.append((frame, value, interp, ease))
        for k, lst in self.keys.items():
            last = {}
            for f, v, i, e in lst:
                last[f] = (v, i, e)
            self.keys[k] = [(f,) + last[f] for f in sorted(last)]
        self.raw_keys = self.keys
        self.keys = {k: bake_keys(lst) for k, lst in self.raw_keys.items()}
        # (object id, property key, interpolator) of the segments bake_keys rewrote, for the report
        self.baked = [(o, pk, i) for (o, pk), lst in self.raw_keys.items() for i in sorted({k[2] for k in lst[:-1]})
                      if i in ("cubicValue", "elastic")]

    def keys_of(self, oid):
        """{property name: key list} for one object."""
        return {prop_name(pk): lst for (o, pk), lst in self.keys.items() if o == oid}


class Project:
    def __init__(self, directory):
        self.dir = os.path.abspath(directory)
        self.name = os.path.basename(self.dir)
        self.yaml = {}
        yml = os.path.join(self.dir, "rive.yaml")
        if os.path.exists(yml):
            for line in open(yml, encoding="utf-8"):
                m = re.match(r"^(\w+):\s*(\S.*)$", line)
                if m:
                    self.yaml[m.group(1)] = m.group(2).strip()
            self.name = self.yaml.get("name", self.name)
        self.roots = []
        self.by_id = {}
        for f in sorted(os.listdir(self.dir)):
            if f.endswith(".rml"):
                self.roots.append(self._parse(os.path.join(self.dir, f)))
        self.artboards = [e for r in self.roots for e in r.children if e.tag == "Artboard"]
        self.assets = {e.id: e for r in self.roots for e in r.children if e.tag.endswith("Asset") and e.id}
        self.viewmodels = [e for r in self.roots for e in r.children if e.tag == "ViewModel"]
        self.animations = {}          # animation id -> Animation
        for ab in self.artboards:
            for a in ab.findall("LinearAnimation"):
                self.animations[a.id] = Animation(a)

    def _parse(self, path):
        text = _AMP.sub("&amp;", open(path, encoding="utf-8").read())
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))     # keep <!-- ae: … --> comments
        parser.feed(text)
        root = parser.close()

        def build(x, parent):
            e = El(x.tag, x.attrib, parent)
            if e.id:
                self.by_id[e.id] = e
            pending = []
            for c in x:
                if c.tag is ET.Comment:
                    if (c.text or "").strip().lower().startswith("ae:"):
                        pending.append(c.text.strip())
                    continue
                child = build(c, e)
                child.ae = pending
                pending = []
                e.children.append(child)
            return e
        return build(root, None)

    # ---- artboard helpers
    def artboard_by_id(self, aid):
        return self.by_id.get(aid)

    def default_artboard(self):
        for ab in self.artboards:
            if ab.get("isComponent") != "true":
                return ab
        return self.artboards[0] if self.artboards else None

    def entry_chain(self, artboard):
        """Animations the default state machine plays from its entry state, following exit-time transitions.
        Returns ([Animation...], loops_back: bool, notes: [str])."""
        notes = []
        sm_id = artboard.get("defaultStateMachineId")
        sms = artboard.findall("StateMachine")
        sm = self.by_id.get(sm_id) if sm_id else (sms[0] if sms else None)
        anims = artboard.findall("LinearAnimation")
        if sm is None:
            if anims:
                return [self.animations[anims[0].id]], False, notes
            return [], False, notes
        layers = sm.findall("StateMachineLayer")
        if len(layers) > 1:
            notes.append(f"state machine '{sm.name}' has {len(layers)} layers: only the first one is converted")
        if not layers:
            return ([self.animations[anims[0].id]] if anims else []), False, notes
        layer = layers[0]
        states = {s.id: s for s in layer.children if s.id}
        entry = layer.find("EntryState")
        chain, seen = [], set()
        self._links = getattr(self, "_links", {})
        links = []                      # per chain element: dict(exit=fraction of the animation, blend=seconds)
        cur = None
        if entry is not None:
            tr = entry.find("StateTransition")
            cur = tr.get("stateToId") if tr is not None else None
        loops = False
        while cur and cur in states and cur not in seen:
            seen.add(cur)
            st = states[cur]
            if st.tag == "AnimationState" and st.get("animationId") in self.animations:
                chain.append(self.animations[st.get("animationId")])
            elif st.tag != "AnimationState":
                notes.append(f"state '{st.name}' ({st.tag}) is not a plain animation state: not converted")
                break
            nxt = None
            link = {"exit": 1.0, "blend": 0.0}
            for tr in st.findall("StateTransition"):
                if tr.get("enableExitTime") == "true" and not tr.findall("TransitionCondition"):
                    nxt = tr.get("stateToId")
                    a = chain[-1]
                    et = tr.num("exitTime", 0)
                    if tr.get("exitTimeIsPercetange") == "true" or tr.get("exitTimeIsPercentage") == "true":
                        link["exit"] = max(0.0, min(1.0, et / 100.0))
                    elif et > 0:
                        link["exit"] = max(0.0, min(1.0, (et / 1000.0) / (a.duration / a.fps)))
                    d = tr.num("duration", 0)
                    if d > 0:
                        link["blend"] = (d / 100.0) * (a.duration / a.fps) if tr.get("durationIsPercentage") == "true" else d / 1000.0
                    break
                elif tr.children or tr.get("enableExitTime") != "true":
                    notes.append(f"transition from '{st.name}' depends on an input/event: left to the editor")
            links.append(link)
            if nxt in seen:
                loops = True
                break
            cur = nxt
        if not chain and anims:
            chain = [self.animations[anims[0].id]]
            links = [{"exit": 1.0, "blend": 0.0}]
        self._links[artboard.id] = links
        return chain, loops, notes

    def chain_links(self, artboard):
        """[{exit, blend}] parallel to entry_chain(artboard)[0] (call entry_chain first)."""
        if not hasattr(self, "_links") or artboard.id not in self._links:
            self.entry_chain(artboard)
        return self._links.get(artboard.id, [])
