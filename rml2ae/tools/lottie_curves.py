"""Read the animation curves of Lottie / Bodymovin .json files (After Effects exports): what the animator keyed.

    python3 -m rml2ae.tools.lottie_curves file.json [file2.json …] [--json out.json]

Same output family as riv_curves: ease catalogue ((x1,y1,x2,y2) = Lottie out/in tangents = CSS/Rive bezier),
segment durations, keyed properties, layer staggers (in-points), recurring patterns, and — Lottie-specific — the
text animators (range selectors, what they animate, offsets) and precomp structure. No rendering, no images.
"""
import json
import os
import sys
from collections import Counter, defaultdict

LAYER_TYPES = {0: "precomp", 1: "solid", 2: "image", 3: "null", 4: "shape", 5: "text", 6: "audio", 13: "camera"}
TRANSFORM = {"a": "anchor", "p": "position", "s": "scale", "r": "rotation", "o": "opacity", "sk": "skew", "sa": "skewAxis",
             "x": "positionX", "y": "positionY", "z": "positionZ"}
TEXT_ANIM = {"t": "tracking", "s": "scale", "p": "position", "r": "rotation", "o": "opacity", "a": "anchor", "fc": "fillColor",
             "sc": "strokeColor", "sw": "strokeWidth", "fh": "fillHue", "fs": "fillSat", "fb": "fillBright", "ls": "lineSpacing"}


def num(v):
    return v[0] if isinstance(v, list) and len(v) == 1 else v


def keys_of(prop, fr):
    """animated property -> [(frame, value, interp, ease)] ; static -> None"""
    if not isinstance(prop, dict) or prop.get("a", 0) != 1 or not isinstance(prop.get("k"), list):
        return None
    ks = prop["k"]
    if not ks or not isinstance(ks[0], dict):
        return None
    out = []
    for j, k in enumerate(ks):
        t = k.get("t", 0)
        v = k.get("s")
        if isinstance(v, list) and len(v) == 1:
            v = v[0]
        if k.get("h") == 1:
            out.append((t, v, "hold", None))
            continue
        o, i = k.get("o"), k.get("i")
        if o and i:
            x1, y1 = num(o.get("x", 0)), num(o.get("y", 0))
            x2, y2 = num(i.get("x", 1)), num(i.get("y", 1))
            if isinstance(x1, list):
                x1, y1, x2, y2 = x1[0], y1[0], x2[0], y2[0]
            ease = (round(float(x1), 3), round(float(y1), 3), round(float(x2), 3), round(float(y2), 3))
            lin = abs(ease[0] - ease[1]) < 0.02 and abs(ease[2] - ease[3]) < 0.02
            out.append((t, v, "linear" if lin else "cubic", None if lin else ease))
        else:
            out.append((t, v, "linear", None))
    return out


def walk_layers(layers, assets, fr, comp="root", depth=0, acc=None, prefix=""):
    """collect tracks: [{comp, layer, ltype, prop, keys, ip, op, st}] + text animators"""
    acc = acc if acc is not None else {"tracks": [], "layers": [], "text": []}
    for L in layers:
        lt = LAYER_TYPES.get(L.get("ty"), str(L.get("ty")))
        name = prefix + L.get("nm", "?")
        acc["layers"].append({"comp": comp, "layer": name, "type": lt, "ip": L.get("ip", 0), "op": L.get("op", 0), "st": L.get("st", 0), "parent": L.get("parent")})
        ks = L.get("ks", {})
        for k, pname in TRANSFORM.items():
            kk = keys_of(ks.get(k), fr)
            if kk:
                acc["tracks"].append({"comp": comp, "layer": name, "type": lt, "prop": pname, "keys": kk})
        # shape layer contents: transforms of groups + path/fill animations (one level of naming)
        for sh in L.get("shapes", []) or []:
            walk_shape(sh, acc, comp, name, fr)
        # text
        if lt == "text":
            td = L.get("t", {})
            doc = td.get("d", {}).get("k", [])
            texts = [d.get("s", {}).get("t") for d in doc] if isinstance(doc, list) else []
            for an in td.get("a", []) or []:
                sel = an.get("s", {})
                props = {}
                for k, pname in TEXT_ANIM.items():
                    if k in an.get("a", {}):
                        p = an["a"][k]
                        kk = keys_of(p, fr)
                        props[pname] = ("keys", kk) if kk else ("static", num(p.get("k")))
                rng = {}
                for k in ("s", "e", "o", "sm", "xe", "ne", "a"):
                    if k in sel:
                        kk = keys_of(sel[k], fr)
                        rng[k] = ("keys", kk) if kk else ("static", num(sel[k].get("k")))
                acc["text"].append({"comp": comp, "layer": name, "texts": texts, "animator": an.get("nm", "?"), "based_on": {1: "chars", 2: "chars-excl-spaces", 3: "words", 4: "lines"}.get(sel.get("b"), sel.get("b")),
                                    "shape": {1: "square", 2: "ramp up", 3: "ramp down", 4: "triangle", 5: "round", 6: "smooth"}.get(sel.get("sh"), sel.get("sh")),
                                    "range": rng, "props": props, "ip": L.get("ip", 0), "op": L.get("op", 0)})
        if lt == "precomp" and depth < 6:
            a = next((x for x in assets if x.get("id") == L.get("refId")), None)
            if a and "layers" in a:
                walk_layers(a["layers"], assets, fr, comp=name, depth=depth + 1, acc=acc, prefix="")
    return acc


def walk_shape(sh, acc, comp, layer, fr, path=""):
    nm = path + "/" + sh.get("nm", sh.get("ty", "?"))
    ty = sh.get("ty")
    if ty == "gr":
        for it in sh.get("it", []):
            walk_shape(it, acc, comp, layer, fr, nm)
    elif ty == "tr":
        for k, pname in TRANSFORM.items():
            kk = keys_of(sh.get(k), fr)
            if kk:
                acc["tracks"].append({"comp": comp, "layer": layer + nm, "type": "shape-group", "prop": pname, "keys": kk})
    elif ty in ("sh", "rc", "el", "sr", "fl", "st", "gf", "gs", "tm", "rd", "rp"):
        for k in ("ks", "p", "s", "r", "o", "c", "w", "e", "sh", "ir", "or", "st", "en"):
            kk = keys_of(sh.get(k), fr)
            if kk:
                acc["tracks"].append({"comp": comp, "layer": layer + nm, "type": "shape-" + ty, "prop": k, "keys": kk})


def scalar_keys(keys):
    """multi-dim values -> first differing dimension as scalar, for pattern analysis"""
    if not keys:
        return None
    if all(isinstance(k[1], (int, float)) for k in keys):
        return keys
    if all(isinstance(k[1], list) for k in keys):
        dims = len(keys[0][1])
        for d in range(dims):
            vals = [k[1][d] for k in keys]
            if max(vals) - min(vals) > 1e-6:
                return [(k[0], k[1][d], k[2], k[3]) for k in keys]
        return [(k[0], k[1][0], k[2], k[3]) for k in keys]
    return None


def analyse(path):
    d = json.load(open(path, encoding="utf-8"))
    fr = d.get("fr", 60)
    acc = walk_layers(d.get("layers", []), d.get("assets", []), fr)
    lines = [f"== {os.path.basename(path)}: {d.get('w')}x{d.get('h')} @ {fr} fps, {(d.get('op', 0) - d.get('ip', 0)) / fr:.2f} s, "
             f"{len(acc['layers'])} layers ({', '.join(f'{k}:{v}' for k, v in Counter(l['type'] for l in acc['layers']).items())}), "
             f"{len(acc['tracks'])} keyed tracks, {len(acc['text'])} text animator(s)"]
    eases, seg, props = Counter(), Counter(), Counter()
    for t in acc["tracks"]:
        props[(t["type"], t["prop"])] += 1
        ks = t["keys"]
        for j in range(len(ks) - 1):
            e = ks[j][3]
            eases[e if e else (ks[j][2],)] += 1
            seg[int(round((ks[j + 1][0] - ks[j][0]) / fr * 1000 / 20) * 20)] += 1
    lines.append("eases (x1,y1,x2,y2 → uses): " + ", ".join(f"{e}:{n}" for e, n in eases.most_common(10)))
    lines.append("segment durations (ms → count): " + ", ".join(f"{k}:{v}" for k, v in sorted(seg.items())[:24]))
    lines.append("keyed properties: " + ", ".join(f"{t}.{p}:{n}" for (t, p), n in props.most_common(14)))
    # layer staggers (in-points within a comp)
    stag = Counter()
    by_comp = defaultdict(list)
    for L in acc["layers"]:
        by_comp[L["comp"]].append(L["ip"])
    for c, ips in by_comp.items():
        ips = sorted(set(ips))
        for j in range(len(ips) - 1):
            ms = (ips[j + 1] - ips[j]) / fr * 1000
            if 0 < ms <= 600:
                stag[int(round(ms / 20) * 20)] += 1
    if stag:
        lines.append("layer in-point staggers (ms → count): " + ", ".join(f"{k}:{v}" for k, v in sorted(stag.items())))
    # patterns
    pats, example = Counter(), {}
    for t in acc["tracks"]:
        if t["prop"] not in ("position", "scale", "rotation", "opacity", "positionX", "positionY", "anchor"):
            continue
        ks = scalar_keys(t["keys"])
        if not ks or not (2 <= len(ks) <= 6):
            continue
        vals = [k[1] for k in ks]
        span = max(vals) - min(vals)
        if span < 1e-6:
            continue
        segs = tuple(int(round((ks[j + 1][0] - ks[j][0]) / fr * 1000 / 10) * 10) for j in range(len(ks) - 1))
        rel = tuple(round((v - vals[0]) / span, 1) for v in vals)
        es = tuple("hold" if k[2] == "hold" else ("lin" if k[3] is None else str(k[3])) for k in ks[:-1])
        key = (t["prop"], segs, rel, es)
        pats[key] += 1
        example.setdefault(key, f"{t['layer']} ({t['comp']})")
    lines.append("patterns (prop | segment ms | values 0..1 | eases | uses | example):")
    for key, n in pats.most_common(14):
        lines.append(f"  {n:4d}  {key[0]:9s} {list(key[1])} {list(key[2])} {list(key[3])}   e.g. {example[key]}")
    # text animators
    for ta in acc["text"]:
        rng = ", ".join(f"{k}={'keys ' + str([(x[0], x[1]) for x in v[1]]) if v[0] == 'keys' else v[1]}" for k, v in ta["range"].items())
        pr = ", ".join(f"{k}={'keys ' + str([(x[0], x[1]) for x in v[1]]) if v[0] == 'keys' else v[1]}" for k, v in ta["props"].items())
        lines.append(f"text animator '{ta['animator']}' on '{ta['layer']}' texts={ta['texts'][:2]} based on {ta['based_on']}, shape {ta['shape']} | range: {rng} | animates: {pr}")
    return "\n".join(lines), acc


def main(argv):
    files = [a for a in argv if not a.startswith("--")]
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    if out_json in files:
        files.remove(out_json)
    everything = {}
    for f in files:
        text, acc = analyse(f)
        print(text)
        print()
        everything[os.path.basename(f)] = acc
    if out_json:
        json.dump(everything, open(out_json, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
