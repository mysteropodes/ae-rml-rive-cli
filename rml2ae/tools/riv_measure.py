#!/usr/bin/env python3
"""riv_measure — mesurer un `.riv` de référence : timing, jeu, interactions.

Pourquoi du Python ici : le CLI Rive **compile** RML → `.riv` et `rive inspect` lit un *projet* RML
(`rive <fichier>.riv --verify` répond « no Rive project … (no rive.yaml) »). Il n'y a pas de commande
`decode`/`dump` : pour mesurer un `.riv` étranger il faut un lecteur du format, et ce lecteur est
`rml2ae.tools.riv_curves`. Ce module est la couche de **mesure** posée dessus — il ne produit pas de scène,
il ne sert qu'à lire un fichier de référence pour en tirer des principes (RML, courbes, durées).

Trois lectures, un seul parse par fichier :

  · `timing`   — quelles courbes, quelles durées, quels staggers, sparse vs baké
  · `acting`   — posé vs glissé, cadence de poses, temps tenu, anticipation, rebonds, déformation, scripts
  · `interact` — listeners, types d'entrée, entrées SM/ViewModel, fondus, exit time, paires in/out

Usage (depuis la racine du dépôt, venv activé) :

    python -m rml2ae.tools.riv_measure <fichier.riv> [...] [options]
    python -m rml2ae.tools.riv_measure 01_RIV_LIBRARY/studies/*/source/*.riv --per-file --json 01_RIV_LIBRARY/studies

  --mode timing|acting|interact|all   ce qu'on mesure (défaut : all)
  --per-file                          écrit survey.txt / acting.txt / interact.txt dans le dossier de l'étude
                                      (le dossier parent de `source/`)
  --json <dir|fichier>                JSON brut : un fichier par mode si c'est un dossier
  --top N                             nombre d'entrées affichées dans le tableau d'eases (timing)

Les sorties et les fiches restent dans `01_RIV_LIBRARY/studies/<slug>/` ; la leçon distillée remonte dans
`motion/MOTION_PRINCIPLES.md` (§13 timing, §14–§15 jeu, §16 interactions).
"""
import json
import os
import re
import statistics as st
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from rml2ae.tools import riv_curves as rc
except ImportError:                      # lancé comme script, pas comme module
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
    from rml2ae.tools import riv_curves as rc

DEFAULT_EASE = (0.42, 0.0, 0.58, 1.0)
MOVABLE = ("x", "y", "scaleX", "scaleY", "rotation", "opacity")
PART = {"rotation": "geste", "scaleX": "poids", "scaleY": "poids", "x": "déplacement", "y": "déplacement",
        "opacity": "matière"}
DEFORM_TYPES = ("Bone", "RootBone", "Skin", "Tendon", "Weight", "CubicWeight", "Vertex", "MeshVertex",
                "ContourMeshVertex")
SCRIPTY = ("ScriptAsset", "ScriptedDrawable", "ScriptedPathEffect", "ScriptedLayout", "ScriptedConstraint",
           "ScriptInputNumber", "ScriptInputString", "ScriptInputTrigger", "ScriptInputBoolean")
IN_RE = re.compile(r"(?i)(?:_|^|\s)(in|on|up|enter|hover|press|select|open|show|start|pop)(?:_|\s|$)")
OUT_RE = re.compile(r"(?i)(?:_|^|\s)(out|off|down|leave|exit|close|hide|stop|rest|release)(?:_|\s|$)")
LOOP_RE = re.compile(r"(?i)(?:_|^|\s)(loop|idle|cycle|pingpong|wait)(?:_|\s|$)")


def is_baked(keys):
    """Une piste bakée (une clé toutes les ~2 frames) est une **valeur**, pas une courbe posée."""
    span = keys[-1][0] - keys[0][0]
    return len(keys) >= 6 and len(keys) / max(1, span + 1) >= 0.5


# ─────────────────────────────── timing ───────────────────────────────
def ease_label(track_ease, interp):
    if interp == "hold":
        return "hold"
    if track_ease is None:
        return "cubic·défaut" if interp == "cubic" else "linear"
    if interp == "linear":
        return "linear"
    if track_ease[0] == "elastic":
        return f"elastic(a{track_ease[1]},p{track_ease[2]})"
    x1, y1, x2, y2 = track_ease[:4]
    d = max(abs(x1 - DEFAULT_EASE[0]), abs(y1 - DEFAULT_EASE[1]), abs(x2 - DEFAULT_EASE[2]), abs(y2 - DEFAULT_EASE[3]))
    tag = "≈défaut" if d < 0.08 else "choisie"
    graph = " graph" if len(track_ease) == 5 else ""
    return f"({x1},{y1},{x2},{y2}){graph} {tag}"


def timing_of(riv, arts, name, top=12):
    out = {"file": name, "version": riv["version"], "artboards": len(arts),
           "animations": 0, "fps": Counter(), "loops": Counter(), "tracks": 0, "sparse": 0, "baked": 0,
           "eases": Counter(), "seg_ms": Counter(), "props": Counter(), "staggers": Counter(),
           "ease_kind": Counter(), "baked_examples": [], "sparse_tracks": 0}
    for ab in arts:
        for a in rc.curves_of(ab):
            fps = a["fps"] or 60
            tracks = [t for t in a["tracks"] if len(t["keys"]) >= 2]
            if not tracks:
                continue
            out["animations"] += 1
            out["fps"][fps] += 1
            out["loops"][rc.LOOPS.get(a["loop"], a["loop"])] += 1
            out["tracks"] += len(tracks)
            groups = defaultdict(list)
            for t in tracks:
                ks = t["keys"]
                span = ks[-1][0] - ks[0][0]
                if is_baked(ks):
                    out["baked"] += 1
                    if len(out["baked_examples"]) < 6 and t["prop"] in MOVABLE:
                        out["baked_examples"].append(
                            f"{t['object'] or t['type']}.{t['prop']} @ {a['name']} ({len(ks)} clés / {span} f)")
                    continue                      # baké : hors du vocabulaire
                out["sparse"] += 1
                out["props"][f"{t['type']}.{t['prop']}"] += 1
                for j in range(len(ks) - 1):
                    f0, _v0, interp, ease = ks[j]
                    f1 = ks[j + 1][0]
                    out["eases"][ease_label(ease, interp)] += 1
                    out["seg_ms"][int(round((f1 - f0) / fps * 1000 / 10) * 10)] += 1
                # staggers : mêmes segments relatifs sur ≥ 3 objets de la même animation
                if t["prop"] in MOVABLE and 2 <= len(ks) <= 6 and all(isinstance(k[1], (int, float)) for k in ks):
                    vals = [k[1] for k in ks]
                    span_v = max(vals) - min(vals)
                    if span_v > 1e-6:
                        segs = tuple(int(round((ks[j + 1][0] - ks[j][0]) / fps * 1000 / 10) * 10)
                                     for j in range(len(ks) - 1))
                        rel = tuple(round((v - vals[0]) / span_v, 1) for v in vals)
                        groups[(t["prop"], segs, rel)].append((ks[0][0], t["oid"], fps))
            for key, members in groups.items():
                if len(members) < 3:
                    continue
                frames = sorted({m[0] for m in members})
                fps = members[0][2]
                for i in range(1, len(frames)):
                    out["staggers"][int(round((frames[i] - frames[i - 1]) / fps * 1000 / 10) * 10)] += 1
    return out


def timing_report(o, top=12):
    L = [f"== {o['file']}  (v{o['version']}, {o['artboards']} artboards, {o['animations']} animations)"]
    L.append("fps " + ", ".join(f"{k}:{v}" for k, v in o["fps"].most_common()) +
             "  |  timelines " + ", ".join(f"{k}:{v}" for k, v in o["loops"].most_common()))
    L.append(f"pistes {o['tracks']} — sparse {o['sparse']} (vocabulaire) / baké {o['baked']}")
    if o["eases"]:
        total = sum(o["eases"].values())
        kinds = Counter()
        for label, n in o["eases"].items():
            kinds["hold" if label == "hold" else "linear" if label == "linear" else
                  "elastic" if label.startswith("elastic") else "≈défaut" if label.endswith("≈défaut") else "choisie"] += n
        L.append("types de segment : " + ", ".join(f"{k} {v} ({v * 100 // max(1, total)} %)" for k, v in kinds.most_common()))
        L.append("eases (pistes sparse) :")
        for label, n in o["eases"].most_common(top):
            L.append(f"    {n:5d}  {label}")
    L.append("durées de segment (ms → count, sparse) : " +
             ", ".join(f"{k}:{v}" for k, v in sorted(o["seg_ms"].items())[:20]))
    if o["staggers"]:
        L.append("staggers mesurés (ms → count) : " + ", ".join(f"{k}:{v}" for k, v in o["staggers"].most_common(10)))
    L.append("propriétés animées (sparse) : " + ", ".join(f"{k}:{v}" for k, v in o["props"].most_common(12)))
    if o["baked_examples"]:
        L.append("exemples de baké (à ne PAS lire comme courbes) : " + " ; ".join(o["baked_examples"]))
    return "\n".join(L)


# ─────────────────────────────── jeu ───────────────────────────────
def classify(t):
    """node = le nœud agit (jeu lisible) · deform = sommet/os/poids · rig = contrainte."""
    if "Constraint" in t["type"]:
        return "rig"
    if any(k in t["type"] for k in DEFORM_TYPES):
        return "deform"
    return "node" if t["prop"] in MOVABLE else "autre"


def net_sign(vals):
    d = vals[-1] - vals[0]
    if abs(d) < 1e-6:
        return 0, max(vals) - min(vals)
    return (1 if d > 0 else -1), abs(d)


def acting_of(riv, arts, name):
    o = {"file": name, "tracks": 0, "tween": 0, "poses": Counter(),
         "hold_frames": 0, "track_frames": 0, "anticipation": 0, "overshoot": 0, "rebounds": [],
         "parts": Counter(), "poses_per_s": [], "micro": Counter(), "longest_hold_ms": 0,
         "fam": Counter(), "fam_baked": Counter(), "deform_eases": Counter(), "scripts": Counter()}
    for oo in riv["objects"]:
        if oo["type"] in SCRIPTY:
            o["scripts"][oo["type"]] += 1
    for ab in arts:
        for a in rc.curves_of(ab):
            fps = a["fps"] or 60
            for t in a["tracks"]:
                ks = t["keys"]
                if len(ks) < 2:
                    continue
                dur = ks[-1][0] - ks[0][0] + 1
                dense = is_baked(ks)
                fam = classify(t)
                o["fam"][fam] += 1
                if dense:
                    o["fam_baked"][fam] += 1
                elif fam == "deform":
                    for j in range(len(ks) - 1):
                        e = ks[j][3]
                        o["deform_eases"]["hold" if ks[j][2] == "hold" else
                                          "linear" if (ks[j][2] == "linear" or e is None) else "courbe"] += 1
                if t["prop"] not in MOVABLE or dense:
                    continue
                if not all(isinstance(k[1], (int, float)) for k in ks):
                    continue
                o["tracks"] += 1
                o["parts"][PART[t["prop"]]] += 1
                o["track_frames"] += dur
                vals = [k[1] for k in ks]
                frames = [k[0] for k in ks]
                o["poses"][min(len(ks), 8)] += 1
                if len(ks) == 2:
                    o["tween"] += 1
                else:
                    o["poses_per_s"].append(len(ks) / max(0.001, dur / fps))
                # temps tenu : clés consécutives de même valeur (ou interpolation hold)
                for j in range(len(ks) - 1):
                    if abs(vals[j + 1] - vals[j]) < 1e-6 or ks[j][2] == "hold":
                        o["hold_frames"] += frames[j + 1] - frames[j]
                        o["longest_hold_ms"] = max(o["longest_hold_ms"],
                                                   round((frames[j + 1] - frames[j]) / fps * 1000))
                s, span_v = net_sign(vals)
                if 3 <= len(ks) <= 8:
                    rng = max(vals) - min(vals)
                    if rng > 1e-6:
                        # anticipation : une clé part à l'opposé du sens général avant de partir
                        for j in range(1, len(ks) - 1):
                            if s and (vals[j] - vals[0]) * s < -0.02 * rng:
                                o["anticipation"] += 1
                                break
                        # rebonds : nombre d'inversions de sens (settle décroissant)
                        dirs = [(vals[j + 1] - vals[j]) > 0 for j in range(len(ks) - 1)
                                if abs(vals[j + 1] - vals[j]) > 1e-6]
                        inv = sum(1 for j in range(1, len(dirs)) if dirs[j] != dirs[j - 1])
                        if inv:
                            o["rebounds"].append(inv)
                        # dépassement : une clé va au-delà de la valeur d'arrivée puis revient
                        over = max(max(vals) - vals[-1], vals[-1] - min(vals)) / rng
                        if 0.02 < over and inv:
                            o["overshoot"] += 1
    return o


def acting_report(o):
    tot = max(1, o["tracks"])
    held = o["hold_frames"] / max(1, o["track_frames"])
    pps = sorted(o["poses_per_s"])
    med = pps[len(pps) // 2] if pps else 0
    L = [f"== {o['file']}"]
    L.append(f"pistes de jeu {o['tracks']} · 2 clés (glissé pur) {o['tween']} ({o['tween'] * 100 // tot} %) · "
             f"{o['tracks'] - o['tween']} posées ({100 - o['tween'] * 100 // tot} %) · poses/s médianes {med:.1f}")
    L.append(f"temps tenu {held * 100:.0f} % de la durée totale · plus long hold {o['longest_hold_ms']} ms")
    L.append("clés par piste " + ", ".join(f"{k}:{v}" for k, v in sorted(o["poses"].items())))
    L.append(f"anticipation {o['anticipation']} · rebond+retour {o['overshoot']} · pistes à rebonds "
             f"{len(o['rebounds'])} · rebonds max {max(o['rebounds']) if o['rebounds'] else 0}")
    L.append("ce qui agit : " + ", ".join(f"{k} {v}" for k, v in o["parts"].most_common()))
    fams = ", ".join(f"{k} {v}" + (f" (dont {o['fam_baked'][k]} bakées)" if o["fam_baked"][k] else "")
                     for k, v in o["fam"].most_common())
    L.append(f"familles de pistes : {fams}")
    if o["deform_eases"]:
        de = o["deform_eases"]
        t = sum(de.values()) or 1
        L.append("déformation (sparse) : " + ", ".join(f"{k} {v * 100 // t} %" for k, v in de.most_common()))
    if o["scripts"]:
        L.append("scripts : " + ", ".join(f"{k} ×{v}" for k, v in o["scripts"].most_common()) +
                 "  (surface lisible ; le code embarqué n'est pas extractible au CLI — voir le corpus)")
    return "\n".join(L)


# ─────────────────────────── interactions ───────────────────────────
def pair_key(name):
    """Nom d'anim → (radical, sens) pour rapprocher « Icon hover » de « Icon hover out »."""
    n = name.strip()
    m = OUT_RE.search(n)
    if m:
        return (n[:m.start()] + n[m.end():]).strip(), "out"
    m = IN_RE.search(n)
    if m:
        return (n[:m.start()] + n[m.end():]).strip(), "in"
    return n, "?"


def interact_of(riv, arts, name):
    o = {"file": name, "listeners": Counter(), "input_types": Counter(),
         "conditions": Counter(), "comparators": Counter(),
         "sm_inputs": Counter(), "vm": Counter(), "sm": {"machines": 0, "layers": 0, "states": 0, "transitions": 0},
         "blend": Counter(), "exit_time": 0, "semantic": 0, "pairs": 0, "ms_in": [], "ms_out": [],
         "pair_examples": [], "artboards_with_sm": 0}
    for oo in riv["objects"]:
        t = oo["type"]
        if "Listener" in t:
            o["listeners"][t] += 1
        if "ListenerInputType" in t:
            o["input_types"][oo["props"].get("listenerTypeValue", "?")] += 1
        if t.startswith("Transition") and ("Condition" in t or "Comparator" in t):
            (o["comparators"] if "Comparator" in t else o["conditions"])[t] += 1
        if t.startswith("StateMachine") and t.endswith(("Bool", "Number", "Trigger", "FireTrigger")):
            o["sm_inputs"][t] += 1
        if t.startswith(("ViewModel", "DataBind", "BindableProperty", "DataConverter")):
            o["vm"][t] += 1
        if t in ("SemanticInput", "SemanticData"):
            o["semantic"] += 1
        if t == "StateMachine":
            o["sm"]["machines"] += 1
        if t == "StateMachineLayer":
            o["sm"]["layers"] += 1
        if t in ("AnimationState", "BlendState1DViewModel", "BlendState1DInput", "BlendStateDirect"):
            o["sm"]["states"] += 1
        if t == "StateTransition":
            o["sm"]["transitions"] += 1
            p = oo["props"]
            d = p.get("duration", 0)
            o["blend"][f"{d}%" if p.get("flags", 0) & 2 else f"{d}ms"] += 1
            if p.get("flags", 0) & 8:
                o["exit_time"] += 1
    # par artboard : le protocole de nommage des timelines d'entrée/sortie
    for ab in arts:
        anims = rc.curves_of(ab)
        if any(x["type"] == "StateMachine" for x in ab["objs"]):
            o["artboards_with_sm"] += 1
        dur = {}
        for a in anims:
            fps = a["fps"] or 60
            if a["duration"]:
                dur[a["name"]] = round(a["duration"] / fps * 1000)
        buckets = {}
        for nm, ms in dur.items():
            k, side = pair_key(nm)
            if side in ("in", "out"):
                buckets.setdefault(k, {})[side] = ms
        for k, sides in buckets.items():
            if "in" in sides and "out" in sides:
                o["pairs"] += 1
                o["ms_in"].append(sides["in"])
                o["ms_out"].append(sides["out"])
                if len(o["pair_examples"]) < 6:
                    o["pair_examples"].append(f"{k or '?'} : in {sides['in']} ms / out {sides['out']} ms")
    return o


def interact_report(o):
    L = [f"== {o['file']}"]
    sm = o["sm"]
    if sm["machines"] or sm["layers"] or sm["states"]:
        L.append(f"état : {o['artboards_with_sm']} artboards avec SM · couches {sm['layers']} · états {sm['states']} · "
                 f"transitions {sm['transitions']} · exit time {o['exit_time']}")
        if o["blend"]:
            L.append("  fondus de transition : " + ", ".join(f"{k}×{v}" for k, v in o["blend"].most_common(8)))
    if o["conditions"] or o["comparators"]:
        L.append("déclencheurs : " + ", ".join(f"{k.replace('Transition', '')} {v}" for k, v in o["conditions"].most_common()) +
                 ("  | comparateurs : " + ", ".join(f"{k.replace('TransitionProperty', '').replace('TransitionValue', '')} {v}"
                                                    for k, v in o["comparators"].most_common()) if o["comparators"] else ""))
    if o["sm_inputs"] or o["vm"]:
        L.append("  entrées SM : " + (", ".join(f"{k.replace('StateMachine', '')} {v}" for k, v in o["sm_inputs"].most_common()) or "—") +
                 " · ViewModel : " + (", ".join(f"{k} {v}" for k, v in sorted(o["vm"].items())) or "—"))
    if o["listeners"]:
        L.append("écoute : " + ", ".join(f"{k} {v}" for k, v in o["listeners"].most_common()))
    if o["input_types"]:
        L.append("  types d'entrée : " + ", ".join(f"{k} {v}" for k, v in o["input_types"].most_common()))
    elif o["listeners"]:
        L.append("  types d'entrée : aucun `ListenerInputType` — le pointeur est poussé dans un input de SM (forme dépréciée)")
    if o["semantic"]:
        L.append(f"mode sémantique : {o['semantic']} objet(s)")
    if o["pairs"]:
        L.append(f"paires in/out nommées : {o['pairs']} · durée in médiane {int(st.median(o['ms_in']))} ms / "
                 f"out médiane {int(st.median(o['ms_out']))} ms")
        for e in o["pair_examples"][:3]:
            L.append(f"    {e}")
    return "\n".join(L)


# ─────────────────────────────── pilote ───────────────────────────────
MODES = ("timing", "acting", "interact")
SUFFIX = {"timing": "survey", "acting": "acting", "interact": "interact"}


def dumpable(o):
    return {k: (dict(v) if isinstance(v, Counter) else v) for k, v in o.items()}


def measure(path, modes, top=12):
    riv = rc.read_riv(path)
    arts = rc.artboards_of(riv["objects"])
    name = os.path.basename(path)
    res = {}
    if "timing" in modes:
        res["timing"] = timing_of(riv, arts, name, top)
    if "acting" in modes:
        res["acting"] = acting_of(riv, arts, name)
    if "interact" in modes:
        res["interact"] = interact_of(riv, arts, name)
    return res


def study_dir(path):
    """`…/studies/<slug>/source/x.riv` → `…/studies/<slug>` (sinon le dossier du fichier)."""
    d = os.path.dirname(os.path.abspath(path))
    return os.path.dirname(d) if os.path.basename(d) == "source" else d


def main(argv):
    files = [a for a in argv if not a.startswith("--")]
    modes, per_file, out_json, top = list(MODES), False, None, 12
    for i, a in enumerate(argv):
        if a == "--mode":
            v = argv[i + 1].lower()
            modes = list(MODES) if v == "all" else [m for m in MODES if m.startswith(v)]
            if not modes:
                sys.exit(f"mode inconnu : {v} (timing | acting | interact | all)")
            if argv[i + 1] in files:
                files.remove(argv[i + 1])
        elif a == "--per-file":
            per_file = True
        elif a == "--json":
            out_json = argv[i + 1]
            if out_json in files:
                files.remove(out_json)
        elif a == "--top":
            top = int(argv[i + 1])
            if argv[i + 1] in files:
                files.remove(argv[i + 1])
    if not files:
        sys.exit(__doc__)

    dump = {m: {} for m in modes}
    for f in files:
        res = measure(f, modes, top)
        for m in modes:
            rep = timing_report(res[m], top) if m == "timing" else \
                  acting_report(res[m]) if m == "acting" else interact_report(res[m])
            print(rep)
            print()
            dump[m][res[m]["file"]] = dumpable(res[m])
            if per_file:
                with open(os.path.join(study_dir(f), f"{SUFFIX[m]}.txt"), "w") as fh:
                    fh.write(rep + "\n")
    if out_json:
        if os.path.isdir(out_json) or out_json.endswith("/"):
            for m in modes:
                p = os.path.join(out_json, f"{SUFFIX[m]}.json")
                json.dump(dump[m], open(p, "w"), indent=1)
                print("wrote", p)
        else:
            m = modes[0]
            json.dump(dump[m], open(out_json, "w"), indent=1)
            print("wrote", out_json)


if __name__ == "__main__":
    main(sys.argv[1:])
