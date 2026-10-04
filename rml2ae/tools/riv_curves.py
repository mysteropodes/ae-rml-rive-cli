"""Read the animation curves of a .riv file (no editor, no CLI): what a motion designer actually keyed.

.riv binary (rive-runtime, MIT): "RIVE", varuint major/minor/fileId, ToC = varuint property keys ending with 0 then
2 bits per key (0 uint, 1 string, 2 double, 3 color), 4 keys per uint32 (low byte only); then objects = varuint typeKey, properties
(varuint key, value) ending with key 0. Objects are a flat list; an Artboard starts a new local id space
(parentId / objectId / interpolatorId = index in that artboard's list, 0 = the artboard).

    python3 -m rml2ae.tools.riv_curves file.riv [file2.riv …] [--json out.json] [--min-keys 2]

Prints, per file: artboards, animations (fps, duration), and a catalogue of the eases used (x1,y1,x2,y2 → count),
the keyed properties by type, typical segment durations, overshoots and staggers — the raw material of a motion
vocabulary. `--json` keeps everything (per animation, per object, per property: [frame, value, interp, ease]).
"""
import json
import os
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = json.load(open(os.path.join(HERE, "..", "schema.json")))
TYPES_BY_KEY = {v["typeKey"]: v for v in SCHEMA["types"].values() if v.get("typeKey") is not None}
KEYS = {int(k): tuple(v) for k, v in SCHEMA["keys"].items()}          # key -> (owner, name, type)


class Reader:
    def __init__(self, data):
        self.d, self.i = data, 0

    def eof(self):
        return self.i >= len(self.d)

    def varuint(self):
        v, shift = 0, 0
        while True:
            b = self.d[self.i]
            self.i += 1
            v |= (b & 0x7F) << shift
            if not (b & 0x80):
                return v
            shift += 7

    def string(self):
        n = self.varuint()
        s = self.d[self.i:self.i + n].decode("utf-8", errors="replace")
        self.i += n
        return s

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.i)[0]
        self.i += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.i)[0]
        self.i += 4
        return v

    def bytes_(self):
        n = self.varuint()
        b = self.d[self.i:self.i + n]
        self.i += n
        return b


def is_a(type_name, base):
    t = SCHEMA["types"].get(type_name)
    return t is not None and (type_name == base or base in t["extends"])


def read_riv(path):
    """-> list of objects {type, typeKey, props{name: value}, raw{key: value}} in file order."""
    r = Reader(open(path, "rb").read())
    assert r.d[:4] == b"RIVE", "not a .riv"
    r.i = 4
    major, minor, file_id = r.varuint(), r.varuint(), r.varuint()
    toc = []
    while True:
        k = r.varuint()
        if k == 0:
            break
        toc.append(k)
    field = {}
    cur, bit = 0, 0
    for k in toc:
        if bit == 0:
            cur = r.u32()
        field[k] = (cur >> bit) & 3
        bit += 2
        if bit == 8:                  # the exporter packs only 4 keys per uint32 (runtime quirk, measured)
            bit = 0
    objects = []
    while not r.eof():
        tk = r.varuint()
        t = TYPES_BY_KEY.get(tk)
        obj = {"type": t["type"] if t else f"type{tk}", "typeKey": tk, "props": {}}
        while True:
            pk = r.varuint()
            if pk == 0:
                break
            owner, name, ptype = KEYS.get(pk, (None, f"key{pk}", None))
            # the ToC (keys a runtime may not know) is authoritative when it lists the key, else the schema type
            fi = field.get(pk)
            if fi is not None:
                if fi == 0:
                    v = r.varuint()
                    if ptype == "bool":
                        v = bool(v)
                elif fi == 1:
                    v = r.string() if ptype == "String" else r.bytes_()
                elif fi == 2:
                    v = r.f32()
                else:
                    v = r.u32()
            elif ptype in ("uint", "Id", "bool", "uint8_t", "int16_t"):
                v = r.varuint()
                if ptype == "bool":
                    v = bool(v)
            elif ptype == "double":
                v = r.f32()
            elif ptype == "String":
                v = r.string()
            elif ptype == "Bytes":
                v = r.bytes_()
            elif ptype in ("Color", "int"):
                v = r.u32()
            else:
                raise ValueError(f"property key {pk} ({name}) unknown and not in the file's ToC at offset {r.i}")
            obj["props"][name] = v
        objects.append(obj)
    return {"version": f"{major}.{minor}", "objects": objects}


def artboards_of(objects):
    """Split the flat list into artboards: [{name, objs:[...], anims:[...]}] with local ids = index in objs."""
    out = []
    cur = None
    for o in objects:
        if o["type"] == "Artboard":
            cur = {"name": o["props"].get("name", "?"), "objs": [o], "width": o["props"].get("width"), "height": o["props"].get("height")}
            out.append(cur)
        elif cur is not None:
            cur["objs"].append(o)
    return out


def curves_of(ab):
    """Animations of one artboard -> [{name, fps, duration, tracks: [{object, type, prop, keys:[[frame, value, interp, ease]]}]}]"""
    objs = ab["objs"]
    anims = []
    anim = ko = kp = None
    for i, o in enumerate(objs):
        t, p = o["type"], o["props"]
        if t == "LinearAnimation":
            anim = {"name": p.get("name", "?"), "fps": p.get("fps", 60), "duration": p.get("duration", 0),
                    "speed": p.get("speed", 1.0), "loop": p.get("loopValue", 0), "tracks": []}
            anims.append(anim)
            ko = kp = None
        elif t == "KeyedObject" and anim is not None:
            oid = p.get("objectId", 0)
            target = objs[oid] if oid < len(objs) else {"type": "?", "props": {}}
            ko = {"object": target["props"].get("name", ""), "type": target["type"], "oid": oid}
        elif t == "KeyedProperty" and ko is not None:
            pk = p.get("propertyKey", 0)
            kp = {"object": ko["object"], "type": ko["type"], "oid": ko["oid"], "prop": KEYS.get(pk, (None, f"key{pk}", None))[1], "pk": pk, "keys": []}
            anim["tracks"].append(kp)
        elif t.startswith("KeyFrame") and kp is not None:
            it = p.get("interpolationType", 0)          # runtime: 0 hold (default, omitted), 1 linear, 2 cubic ease, 3 cubic value (graph), 4 elastic
            interp = {0: "hold", 1: "linear", 2: "cubic", 3: "cubic", 4: "elastic"}.get(it, str(it))
            ease = None
            iid = p.get("interpolatorId")
            if iid is not None and iid < len(objs):
                ip = objs[iid]["props"]
                if objs[iid]["type"] in ("CubicEaseInterpolator", "CubicValueInterpolator", "CubicInterpolator"):
                    # defaults omitted in the file = the runtime's (0.42, 0, 0.58, 1) = ease in-out
                    ease = [round(ip.get("x1", 0.42), 3), round(ip.get("y1", 0.0), 3), round(ip.get("x2", 0.58), 3), round(ip.get("y2", 1.0), 3)]
                    if objs[iid]["type"] == "CubicValueInterpolator":
                        ease.append("graph")           # graph-editor curve: y handles in value space ((0,0,1,1) = a straight line)
                elif objs[iid]["type"] == "ElasticInterpolator":
                    ease = ["elastic", ip.get("amplitude", 1.0), ip.get("period", 1.0), ip.get("easingValue", 0)]
            v = p.get("value")
            kp["keys"].append([p.get("frame", 0), v, interp, ease])
    return anims


def summarize(path, ab_list, min_keys=2):
    eases = Counter()
    props = Counter()
    seg_ms = Counter()
    overshoots = []
    n_tracks = n_keys = n_dense = 0
    lines = [f"== {os.path.basename(path)}"]
    for ab in ab_list:
        anims = curves_of(ab)
        lines.append(f"artboard '{ab['name']}' {ab.get('width')}x{ab.get('height')} : {len(anims)} animation(s), {len(ab['objs'])} objects")
        for a in anims:
            tracks = [t for t in a["tracks"] if len(t["keys"]) >= min_keys]
            if not tracks:
                continue
            fps = a["fps"] or 60
            lines.append(f"  · {a['name']}: {a['duration']} f @ {fps} fps = {a['duration'] / fps:.2f} s, {len(tracks)} keyed tracks")
            for t in tracks:
                n_tracks += 1
                props[(t["type"], t["prop"])] += 1
                ks = t["keys"]
                n_keys += len(ks)
                if len(ks) >= 6 and len(ks) / max(1, ks[-1][0] - ks[0][0] + 1) >= 0.5:
                    n_dense += 1                 # baked / sampled motion: a key every frame or two, not an intention
                for j in range(len(ks) - 1):
                    f0, v0, interp, ease = ks[j]
                    f1, v1 = ks[j + 1][0], ks[j + 1][1]
                    if interp == "cubic" and ease and ease[0] != "elastic":
                        eases[tuple(ease)] += 1
                    elif interp in ("linear", "hold"):
                        eases[(interp,)] += 1
                    elif ease and ease[0] == "elastic":
                        eases[("elastic", ease[1], ease[2])] += 1
                    seg_ms[int(round((f1 - f0) / fps * 1000 / 20) * 20)] += 1
                # overshoot: a value that goes past its final value then comes back (scale / position pops)
                # overshoot: a settle past the final value then back (scale / position / rotation pops), 3-6 keys
                if t["prop"] in ("scaleX", "scaleY", "x", "y", "rotation", "opacity") and 3 <= len(ks) <= 6 and "Vertex" not in t["type"] and \
                        all(isinstance(k[1], (int, float)) for k in ks):
                    vals = [k[1] for k in ks]
                    lo, hi, first, last = min(vals), max(vals), vals[0], vals[-1]
                    span = abs(last - first)
                    if span > 1e-6:
                        over = max(hi - max(first, last), min(first, last) - lo) / span
                        if 0.02 < over < 1.0:
                            overshoots.append((round(over * 100), t["object"] or t["type"], t["prop"], a["name"], round((ks[-1][0] - ks[0][0]) / fps * 1000)))
    lines.append(f"tracks {n_tracks}, keys {n_keys}" + (f", dense (≥ 0.5 key/frame, baked — not curves to learn) {n_dense}" if n_dense else ""))
    lines.append("eases (x1,y1,x2,y2 → uses):")
    for e, n in eases.most_common(14):
        lines.append(f"  {n:4d}  {e}")
    lines.append("segment durations (ms → count): " + ", ".join(f"{k}:{v}" for k, v in sorted(seg_ms.items())[:24]))
    lines.append("keyed properties: " + ", ".join(f"{t}.{p}:{n}" for (t, p), n in props.most_common(16)))
    if overshoots:
        overshoots.sort(reverse=True)
        lines.append("overshoots (% of the travel, object.prop @ animation, total ms): " + "; ".join(f"{o}% {ob}.{p} @ {an} ({ms} ms)" for o, ob, p, an, ms in overshoots[:12]))
    return "\n".join(lines), {"eases": {str(k): v for k, v in eases.items()}, "props": {f"{t}.{p}": n for (t, p), n in props.items()},
                              "segment_ms": dict(seg_ms), "overshoots": overshoots}


RIG = ("Bone", "RootBone", "Skin", "Tendon", "Weight", "CubicWeight")
DRAW = ("Shape", "PointsPath", "Rectangle", "Ellipse", "Polygon", "Star", "Triangle", "Image", "Mesh", "Text", "NestedArtboard", "Node",
        "Solo", "LayoutComponent", "ScriptedDrawable", "ClippingShape", "TrimPath", "Feather", "DashPath")
LOOPS = {0: "oneShot", 1: "loop", 2: "pingPong"}


def structure(path, objects, ab_list):
    """How the file is built: rig, state machines, transitions (blend times!), data binding, listeners, events."""
    c = Counter(o["type"] for o in objects)
    lines = [f"-- structure of {os.path.basename(path)}: {len(objects)} objects"]
    lines.append("drawing: " + ", ".join(f"{t}:{c[t]}" for t in DRAW if c[t]))
    cons = {t: n for t, n in c.items() if is_a(t, "Constraint")}
    rig = {t: c[t] for t in RIG if c[t]}
    if rig or cons:
        lines.append("rig: " + ", ".join(f"{t}:{n}" for t, n in rig.items()) + (" | constraints: " + ", ".join(f"{t}:{n}" for t, n in cons.items()) if cons else ""))
    scripts = {t: n for t, n in c.items() if t.startswith("Script") or t == "ScriptAsset"}
    if scripts:
        lines.append("scripts: " + ", ".join(f"{t}:{n}" for t, n in scripts.items()))
    assets = {t: n for t, n in c.items() if t.endswith("Asset")}
    if assets:
        lines.append("assets: " + ", ".join(f"{t}:{n}" for t, n in assets.items()))
    vm = {t: n for t, n in c.items() if t.startswith("ViewModel") or t.startswith("DataBind") or t.startswith("DataConverter") or t.startswith("Bindable")}
    if vm:
        lines.append("data binding: " + ", ".join(f"{t}:{n}" for t, n in sorted(vm.items())))
    lst = {t: n for t, n in c.items() if "Listener" in t}
    if lst:
        lines.append("listeners: " + ", ".join(f"{t}:{n}" for t, n in sorted(lst.items())))
    ev = {t: n for t, n in c.items() if t in ("Event", "AudioEvent", "KeyFrameCallback", "StateMachineFireEvent")}
    if ev:
        lines.append("events: " + ", ".join(f"{t}:{n}" for t, n in ev.items()))
    # state machines
    for ab in ab_list:
        objs = ab["objs"]
        sms = [o for o in objs if o["type"] == "StateMachine"]
        loops = Counter()
        fpss = Counter()
        for o in objs:
            if o["type"] == "LinearAnimation":
                loops[LOOPS.get(o["props"].get("loopValue", 0), "?")] += 1
                fpss[o["props"].get("fps", 60)] += 1
        lines.append(f"artboard '{ab['name']}': animations {sum(loops.values())} ({', '.join(f'{k}:{v}' for k, v in loops.items())}; fps {dict(fpss)}), "
                     f"state machines {len(sms)}, layers {sum(1 for o in objs if o['type'] == 'StateMachineLayer')}, "
                     f"states {sum(1 for o in objs if o['type'] == 'AnimationState')} anim / {sum(1 for o in objs if o['type'].startswith('Blend'))} blend, "
                     f"transitions {sum(1 for o in objs if o['type'] == 'StateTransition')}")
        # transitions: blend durations + what triggers them
        durs = Counter()
        exit_t = 0
        conds = Counter()
        for o in objs:
            if o["type"] == "StateTransition":
                p = o["props"]
                d = p.get("duration", 0)
                if p.get("flags", 0) & 2:                 # StateTransitionFlags: 1 disabled, 2 duration %, 4 exit time %, 8 exit time on
                    durs[f"{d}%"] += 1
                else:
                    durs[f"{d}ms"] += 1
                if p.get("flags", 0) & 8:
                    exit_t += 1
            elif is_a(o["type"], "TransitionCondition") or "Comparator" in o["type"]:
                conds[o["type"]] += 1
        if durs:
            lines.append("  transition blend durations: " + ", ".join(f"{k}:{v}" for k, v in durs.most_common(10)) + f" ; with exit time: {exit_t}")
        if conds:
            lines.append("  transition conditions: " + ", ".join(f"{k}:{v}" for k, v in conds.most_common(8)))
        inputs = Counter(o["type"] for o in objs if o["type"].startswith("StateMachine") and o["type"].endswith(("Bool", "Number", "Trigger")))
        if inputs:
            lines.append("  inputs: " + ", ".join(f"{k}:{v}" for k, v in inputs.items()))
        # stagger: same property keyed on several objects in one animation, first key offsets
        stag = Counter()
        for a in curves_of(ab):
            fps = a["fps"] or 60
            by_prop = defaultdict(list)
            for t in a["tracks"]:
                if len(t["keys"]) >= 2:
                    by_prop[t["prop"]].append(t["keys"][0][0])
            for prop, starts in by_prop.items():
                starts = sorted(set(starts))
                for j in range(len(starts) - 1):
                    d = (starts[j + 1] - starts[j]) / fps * 1000
                    if 0 < d <= 400:
                        stag[int(round(d / 20) * 20)] += 1
        if stag:
            lines.append("  staggers between objects (ms → count): " + ", ".join(f"{k}:{v}" for k, v in sorted(stag.items())))
    return "\n".join(lines)


def recipes(ab_list, top=18):
    """Short tracks (3-6 keys on scale / position / rotation / opacity) normalised into patterns:
    (prop, [segment ms…], [value deltas relative to the travel…], [eases…]) → count. The motion vocabulary."""
    pats = Counter()
    example = {}
    for ab in ab_list:
        for a in curves_of(ab):
            fps = a["fps"] or 60
            for t in a["tracks"]:
                ks = t["keys"]
                if t["prop"] not in ("scaleX", "scaleY", "x", "y", "rotation", "opacity") or not (2 <= len(ks) <= 6) or "Vertex" in t["type"]:
                    continue
                if not all(isinstance(k[1], (int, float)) for k in ks):
                    continue
                vals = [k[1] for k in ks]
                span = max(vals) - min(vals)
                if span < 1e-6:
                    continue
                segs = tuple(int(round((ks[j + 1][0] - ks[j][0]) / fps * 1000 / 10) * 10) for j in range(len(ks) - 1))
                rel = tuple(round((v - vals[0]) / span, 1) for v in vals)
                eases = []
                for k in ks[:-1]:
                    e = k[3]
                    if k[2] == "hold":
                        eases.append("hold")
                    elif k[2] == "linear" or e is None:
                        eases.append("lin")
                    elif e[0] == "elastic":
                        eases.append(f"elastic(a{e[1]},p{e[2]})")
                    elif len(e) == 5:
                        eases.append(f"graph({e[0]},{e[1]},{e[2]},{e[3]})")
                    else:
                        eases.append(f"({e[0]},{e[1]},{e[2]},{e[3]})")
                key = (t["prop"], segs, rel, tuple(eases))
                pats[key] += 1
                example.setdefault(key, f"{t['object'] or t['type']} @ {a['name']} ({ab['name']})")
    lines = ["patterns (prop | segment ms | values relative to the travel 0..1 | eases | uses | example):"]
    for key, n in pats.most_common(top):
        prop, segs, rel, eases = key
        lines.append(f"  {n:4d}  {prop:8s} {list(segs)} {list(rel)} {list(eases)}   e.g. {example[key]}")
    return "\n".join(lines)


def main(argv):
    files = [a for a in argv if not a.startswith("--")]
    out_json = None
    min_keys = 2
    for i, a in enumerate(argv):
        if a == "--json":
            out_json = argv[i + 1]
        if a == "--min-keys":
            min_keys = int(argv[i + 1])
    if out_json in files:
        files.remove(out_json)
    everything = {}
    for f in files:
        riv = read_riv(f)
        abs_ = artboards_of(riv["objects"])
        text, stats = summarize(f, abs_, min_keys)
        print(text)
        print(structure(f, riv["objects"], abs_))
        print(recipes(abs_))
        print()
        if out_json:
            everything[os.path.basename(f)] = {"version": riv["version"], "stats": stats,
                                               "artboards": [{"name": ab["name"], "width": ab.get("width"), "height": ab.get("height"), "animations": curves_of(ab)} for ab in abs_]}
    if out_json:
        json.dump(everything, open(out_json, "w"), indent=1)
        print("wrote", out_json)


if __name__ == "__main__":
    main(sys.argv[1:])
