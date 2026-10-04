"""RML (Rive CLI scene) -> a small object model, typed by the Rive schema (schema.json, from `rive schema --json`).

Nothing here knows about After Effects. `Project.load(dir)` reads rive.yaml + scene.rml (+ any *.rml next to it),
keeps every element (tag, attributes, children, id), and indexes: objects by id, artboards, assets, animations
(keys per (object id, property key)), state machines, view models.
"""
import json
import os
import re
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = json.load(open(os.path.join(HERE, "schema.json")))
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


class Animation:
    """A LinearAnimation: keys[(object id, property key)] = [(frame, value, interp, ease)]."""

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
                    ci = kf.find("CubicEaseInterpolator")
                    if interp != "hold":
                        # the runtime: a non-hold key eases through its interpolator child, else it is linear
                        if ci is not None:
                            interp = "cubic"
                            # omitted attributes are the runtime defaults (measured: an empty <CubicEaseInterpolator/>
                            # eases like 0.42, 0, 0.58, 1)
                            ease = (ci.num("x1", 0.42), ci.num("y1", 0.0), ci.num("x2", 0.58), ci.num("y2", 1.0))
                        elif kf.children:
                            self.unsupported.append((oid, pk, kf.children[0].tag))
                            interp = "linear"
                        else:
                            interp = "linear"
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
            for line in open(yml):
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
