"""RML model -> After Effects ExtendScript (.jsx) + conversion report.

One comp per (artboard, animation) — the animation the artboard's default state machine plays from its entry state;
a chain of exit-time states becomes a sequence comp. Nested artboards are precomps (time-remapped when the parent
scrubs them). Nodes are nulls, shapes are shape layers, images footage, texts text layers with animators.
"""
import math
import os
import re

from .model import Project, Animation, El, argb, blend_name, is_a, prop_name
from .jsx import Writer, js, PRELUDE, TIDY_JSX, TIDY_BUILD_JSX
from .report import Report
from . import replay as RP
from . import riveshader as RS
from . import rig as RIG

ASCENT = 0.968          # Rive: first baseline = box top + ascent * size (Montserrat hhea, measured); per-font below
BLOCK_H = 1.164         # measured: bounds height of a one-line text = lineHeight*(n-1) + 1.164*size (see guidelines §5)
AE_BOX_BASELINE = 0.743 # measured (Montserrat, AE 2026): first baseline of a box text = box top + 0.743*size


def bezier_progress(x, x1, y1, x2, y2):
    lo, hi = 0.0, 1.0
    for _ in range(40):
        t = (lo + hi) / 2
        bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3
        if bx < x:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3


def value_at(lst, f):
    """Rive key list [(frame, value, interp, ease)] evaluated at frame f (numbers only)."""
    if not lst:
        return None
    if f <= lst[0][0]:
        return lst[0][1]
    for i in range(len(lst) - 1):
        f0, v0, interp, ease = lst[i]
        f1, v1 = lst[i + 1][0], lst[i + 1][1]
        if f0 <= f < f1:
            if interp == "hold":
                return v0
            t = (f - f0) / (f1 - f0)
            if interp == "cubic" and ease:
                t = bezier_progress(t, *ease)
            return v0 + (v1 - v0) * t
    return lst[-1][1]


def round_corners(verts, ins, outs, radii, closed):
    """Bake StraightVertex radii: a rounded corner becomes two vertices joined by a circular-arc bezier (Rive semantics)."""
    n = len(verts)
    V, I, O = [], [], []
    for i in range(n):
        r = radii[i]
        edge = closed or (0 < i < n - 1)
        if r <= 0 or not edge or n < 3:
            V.append(verts[i]); I.append(ins[i]); O.append(outs[i])
            continue
        P = verts[i]
        A, B = verts[(i - 1) % n], verts[(i + 1) % n]
        ux, uy = A[0] - P[0], A[1] - P[1]
        vx, vy = B[0] - P[0], B[1] - P[1]
        la, lb = math.hypot(ux, uy), math.hypot(vx, vy)
        if la < 1e-6 or lb < 1e-6:
            V.append(P); I.append([0, 0]); O.append([0, 0])
            continue
        ux, uy, vx, vy = ux / la, uy / la, vx / lb, vy / lb
        cos_t = max(-1.0, min(1.0, ux * vx + uy * vy))
        theta = math.acos(cos_t)
        if theta < 1e-3 or abs(theta - math.pi) < 1e-3:
            V.append(P); I.append([0, 0]); O.append([0, 0])
            continue
        t = min(r / math.tan(theta / 2), la / 2, lb / 2)      # distance from the corner to the tangent points
        rr = t * math.tan(theta / 2)                           # effective radius
        phi = math.pi - theta                                  # arc angle
        h = 4.0 / 3.0 * math.tan(phi / 4) * rr                 # bezier handle length for that arc
        P1 = [P[0] + ux * t, P[1] + uy * t]
        P2 = [P[0] + vx * t, P[1] + vy * t]
        V.append(P1); I.append([0, 0]); O.append([-ux * h, -uy * h])
        V.append(P2); I.append([-vx * h, -vy * h]); O.append([0, 0])
    return V, I, O


def kind_of(interp, ease):
    if interp == "hold":
        return ("h", 0, 0, 0, 0)
    if interp == "cubic" and ease:
        x1, y1, x2, y2 = ease
        # a vertical handle (x1 = 0 with y1 != 0, x2 = 1 with y2 != 1) needs a finite AE influence: 0.1 %, the speed
        # computed from it (jsx keys()); written here so the incremental build sees exactly these rows change
        if x1 < 0.001 and abs(y1) > 1e-9:
            x1 = 0.001
        if x2 > 0.999 and abs(1 - y2) > 1e-9:
            x2 = 0.999
        return ("c", x1, y1, x2, y2)
    return ("l", 0, 0, 0, 0)


class Ctx:
    """What the element emitters need: the comp, the animation, the frame scale, the artboard."""

    def __init__(self, conv, comp, artboard, anim, carry=None):
        self.conv, self.comp, self.artboard, self.anim = conv, comp, artboard, anim
        self.fscale = (conv.fps / anim.fps / (anim.speed or 1.0)) if anim else 1.0
        self.carry = carry or {}      # values left by the previous animations of a state chain (unkeyed remaps keep them)

    def keys(self, oid, prop):
        """Key list of (object id, property name) in this animation; a value left by the previous state of a chain
        becomes a single hold key; None when neither exists."""
        if not self.anim or not oid:
            return None
        for (o, pk), lst in self.anim.keys.items():
            if o == oid and prop_name(pk) == prop:
                return lst
        if (oid, prop) in self.carry:
            return [(0, self.carry[(oid, prop)], "hold", None)]
        return None

    def rows(self, lst, fn=None):
        """Key list -> JS rows for keys() (frames rescaled to the comp fps)."""
        out = []
        for f, v, interp, ease in lst:
            out.append([f * self.fscale, fn(v) if fn else v] + list(kind_of(interp, ease)))
        return js(out)

    def rows2(self, la, lb, fn):
        """Two 1-D key lists -> one 2-D list on the union of frames. A side with one key (a static value, a placeholder
        or a carried value) takes the keyed side's frames and eases: exact, and no frame-0 key out of a placeholder
        (teaser-test 'One 1': only scaleY keyed, its pop eases were lost to the placeholder's 'linear')."""
        la, lb = list(la or []), list(lb or [])
        if len(la) <= 1 and len(lb) > 1:
            a0 = la[0][1] if la else value_at(lb, lb[0][0]) * 0
            la = [(f, a0, i, e) for f, _v, i, e in lb]
        elif len(lb) <= 1 and len(la) > 1:
            b0 = lb[0][1] if lb else value_at(la, la[0][0]) * 0
            lb = [(f, b0, i, e) for f, _v, i, e in la]
        frames = sorted(set(k[0] for k in la) | set(k[0] for k in lb))
        kinds = {k[0]: kind_of(k[2], k[3]) for k in la}
        out = []
        for f in frames:
            out.append([f * self.fscale, fn(value_at(la, f), value_at(lb, f))] + list(kinds.get(f, ("l", 0, 0, 0, 0))))
        return js(out)


class Unmergeable(Exception):
    pass


class ChainAnimation:
    """A state-machine chain (entry → A → B → …, full-duration exits, no blend, one fps) as ONE animation: each
    state's keys shifted by its start (audit F13: one comp per artboard instead of one per state, each rebuilding the
    whole artboard). Rive semantics kept: a state keying a property sets it from its own t=0 (first value held before
    its first key, the previous segment ends on a hold); a state not keying it keeps what the previous one left; a
    property keyed only from a later state shows its static value before (a hold key at 0)."""
    chain = True

    def __init__(self, project, ab, states):
        self.id = "chain." + (ab.id or ab.name)
        self.name = ab.name
        self.el = None
        self.fps = states[0].fps
        self.loop = "oneShot"
        self.speed = 1.0
        self.quantize = False
        self.unsupported = [u for a in states for u in a.unsupported]
        self.windows = []                       # (animation id, start frame, end frame)
        o = 0
        for a in states:
            self.windows.append((a.id, o, o + a.duration))
            o += a.duration
        self.duration = o
        self.keys = {}
        tracks = []
        for a in states:
            for k in a.keys:
                if k not in tracks:
                    tracks.append(k)
        for k in tracks:
            out = []
            for (aid, o, e), a in zip(self.windows, states):
                lst = a.keys.get(k)
                if not lst:
                    continue
                if any(f > a.duration for f, _, _, _ in lst):
                    raise Unmergeable(f"{a.name}: a key after the end of the state")
                if not out and o > 0:
                    out.append((0, self.static(project, k, lst[0][1]), "hold", None))
                if out:
                    f, v, _, _ = out[-1]
                    out[-1] = (f, v, "hold", None)       # the next state's value replaces it, no interpolation
                if lst[0][0] > 0:
                    out.append((o, lst[0][1], "hold", None))
                out += [(o + f, v, i, ea) for f, v, i, ea in lst]
            last = {}
            for f, v, i, ea in out:                      # a later state's key wins at a shared frame
                last[f] = (v, i, ea)
            self.keys[k] = [(f,) + last[f] for f in sorted(last)]

    @staticmethod
    def static(project, k, sample):
        oid, pk = k
        el = project.by_id.get(oid)
        name = prop_name(pk)
        raw = el.get(name) if el is not None else None
        if isinstance(sample, bool):
            return raw == "true" if raw is not None else False
        if isinstance(sample, (int, float)):
            if raw is None:
                return {"scaleX": 1.0, "scaleY": 1.0, "opacity": 1.0}.get(name, 0.0)
            return float(raw)
        if isinstance(sample, tuple) and raw is not None:
            return argb(raw)
        if raw is None:
            raise Unmergeable(f"no static value for {name} of {oid}")
        return raw


class Converter:
    def __init__(self, project: Project, out_dir, fps=None, main=None, keep_project=False, replay=True, log=print, replace=False,
                 layout="industry"):
        self.p = project
        self.layout = layout          # 'industry': shapes grouped in shape layers, no identity nulls; 'raw': one layer per element
        self._fold = {}               # leaf id -> the Node whose transform its layer carries (industry layout)
        self.offsets = {}             # element id -> [dx, dy] of dissolved nodes carried in its layer position (ae pull)
        self.chains = {}              # merged chain animation id -> [(animation id, start frame, end frame)] (ae pull)
        self._absorbed = {}           # id(element) -> (dx, dy, [host Nodes]) dissolved above it (audit F14)
        self.static_op_parents = set()  # JS vars of nulls whose opacity (and their ancestors') is always 100 %
        self.pruned = {}              # (artboard, animation) -> elements not built: they draw nothing there
        self._rig_cache = {}
        self.group_map = {}           # layer tag -> [[group index path], rive id] (vector groups that stand for elements)
        self.fold_opacity = {}        # folded node id -> its leaf's opacity (the layer shows their product)
        self.out_dir = out_dir
        self.replace = replace
        self.incremental_mode = False
        self.comp_names = {}      # comp tag -> display name (incremental reports)
        self.solo_kids = {}       # Solo id -> [child ids] (index shown by the "Solo active" slider)
        self.manifest, self.old_manifest, self.stats = {}, {}, {}
        self.manifest_path = os.path.join(out_dir, project.name + ".manifest.json")
        self.replay = replay
        self.log_fn = log
        self.vm_props = {}        # ViewModel property id -> dict(vm, name, kind, value, expr)
        self.layer_of = {}        # element id -> js layer var (per comp; refreshed as comps are built)
        self.all_animations = False
        self.loop_duration = 10.0 # seconds a looping main artboard is unrolled to (--duration)
        self.top_name = None      # name of the comp to render (wrapper "(looped)" / "(stepped)" when created)
        self.vm_comps = {}        # ViewModel id -> comp name
        self.W = Writer()
        self.rep = Report(project.name)
        self.comps = {}           # (artboard id, animation id) -> js var
        self.seq = {}             # artboard id -> js var (sequence comp)
        self.footage = {}
        self.fonts = {}           # FontAsset id -> (postscript name, file, metrics)
        self.main_name = main
        self.keep = keep_project
        import collections
        fps_count = collections.Counter(a.fps for a in project.animations.values())
        self.fps = fps or (fps_count.most_common(1)[0][0] if fps_count else 60)
        self.durs = {}
        self.log = os.path.join(out_dir, project.name + ".ae.log")
        self.aep = os.path.join(out_dir, project.name + ".aep")
        self.warned = set()

    # ------------------------------------------------------------------ comp blocks (incremental rebuilds)
    def mark(self, tag, var, name=None):
        self.comp_names[tag] = name or tag
        self.W(f"//<comp {tag} {var}>")

    def unmark(self):
        self.W("//</comp>")

    _VARRE = re.compile(r"\b(c|seq|lp|stp|vmc|sub|root|clp|nul|shp|img|txt|wrd|crd|row|nst|seqL|bone|an|gimg|sl|au|sa|fxc|pc|ft|ph|swp|line|vmc|grp|fth)\d+(F?)\b")

    def render(self, text, old, full=False):
        """Final pass over the generated script. The generator leaves markers:
            //<comp tag var> … //</comp>              a comp (headless: sequence/wrapper/VM comps)
            //<head var> … //</head>                  comp creation + background, inside a comp block
            //<el tag compvar parentvar> … //</el tag layervar>   one element (its layers carry `tag`)
        Full build: every element runs, wrapped in try/catch, and tags its layers. Incremental build (`old` = the
        manifest of the previous build): unchanged comp heads and elements are looked up instead of rebuilt —
            element own lines unchanged and its layer still there -> kept (children checked in turn);
            element changed (or its layer gone)                   -> its whole layer subtree removed and rebuilt at the same
                                                                     place in the stack; children rebuilt with it;
            comp head changed                                      -> whole comp rebuilt, users of the old comp relinked;
            element / comp gone from the RML                       -> its layers / the comp removed.
        Returns (script text, manifest {key: hash}) ; keys: comp tag, 'comptag#head', 'comptag#eltag'."""
        import hashlib
        import json as _json
        manifest = {}
        self.stats = {"comps_kept": [], "comps_rebuilt": [], "els_kept": 0, "els_rebuilt": 0, "els_removed": 0, "comps_removed": []}
        seq = [0]

        class Block:
            def __init__(self, kind, tag=None, var=None, comp=None, parent=None):
                self.kind, self.tag, self.var, self.comp, self.parent = kind, tag, var, comp, parent
                self.items = []

        root = Block("root")
        stack = [root]
        for line in text.split("\n"):
            if line.startswith("//<comp "):
                _, tag, var = line[:-1].split(" ", 2)
                b = Block("comp", tag, var); stack[-1].items.append(b); stack.append(b)
            elif line.startswith("//<head "):
                b = Block("head", var=line[:-1].split(" ", 1)[1]); stack[-1].items.append(b); stack.append(b)
            elif line.startswith("//<el "):
                _, tag, comp, parent = line[:-1].split(" ", 3)
                b = Block("el", tag, None, comp, parent); stack[-1].items.append(b); stack.append(b)
            elif line.startswith("//</el "):
                _, tag, var = line[:-1].split(" ", 2)
                stack.pop().var = var
            elif line in ("//</comp>", "//</head>"):
                stack.pop()
            else:
                stack[-1].items.append(line)

        def own_hash(b):
            own = [it for it in b.items if isinstance(it, str)]
            norm = "\n".join(self._VARRE.sub(r"\1#\2", l) for l in own)
            return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]

        def subtree(b):
            """element tags of b and of its element descendants in the SAME comp"""
            out = [b.tag] if b.kind == "el" else []
            for it in b.items:
                if isinstance(it, Block) and it.kind == "el":
                    out += subtree(it)
            return out

        def el_keys(comp_tag, b):
            out = []
            for it in b.items:
                if isinstance(it, Block) and it.kind == "el":
                    out.append(f"{comp_tag}#{it.tag}")
                    out += el_keys(comp_tag, it)
            return out

        def nid():
            seq[0] += 1
            return str(seq[0])

        def wrap(lines, tag):
            if not any(l.strip() for l in lines):
                return list(lines)
            return ["try {"] + lines + [f'}} catch (eE) {{ log({js("element FAILED " + tag)} + ": " + eE.toString() + " line " + eE.line); }}']

        def render_el(b, comp_tag, mode, prev_tags, parent_tags):
            """mode: 'full' (build, no placement) | 'check' (keep if unchanged) | 'force' (rebuild inside a rebuilt parent)"""
            if not any(isinstance(it, Block) or it.strip() for it in b.items):
                return []
            key = f"{comp_tag}#{b.tag}"
            h = own_hash(b)
            manifest[key] = h
            if mode == "check" and old.get(key) != h:
                mode = "place"
            n = nid()
            sub = subtree(b)
            main = "null" if b.var == "__none" else f'((typeof {b.var} !== "undefined") ? {b.var} : null)'
            out = []
            if mode == "check":
                self.stats["els_kept"] += 1
                if b.var != "__none":
                    out.append(f'var {b.var} = layerTag({b.comp}, {js(b.tag)}); var __rb{n} = !{b.var};')
                else:
                    out.append(f'var __rb{n} = true;')      # nothing to look up: always redone (cheap, no layer)
                guard = f"if (__rb{n}) "
                out.append(f'{guard}{{ var __bl{n} = anchor({b.comp}, {js(sub)}, {js(prev_tags)}, {js(parent_tags)}); removeTagged({b.comp}, {js(sub)}); var __nb{n} = layerIds({b.comp}); log({js("rebuilt (layer missing): " + b.tag)}); }}')
            elif mode == "place":
                self.stats["els_rebuilt"] += 1
                guard = ""
                out.append(f'var __bl{n} = anchor({b.comp}, {js(sub)}, {js(prev_tags)}, {js(parent_tags)}); removeTagged({b.comp}, {js(sub)}); var __nb{n} = layerIds({b.comp}); log({js("rebuilt: " + b.tag)});')
            else:
                guard = ""
                out.append(f'var __nb{n} = layerIds({b.comp});')
            child_mode = "check" if mode == "check" else "force"
            last_prev = []
            run = []

            def flush():
                if run:
                    lines = wrap(run, b.tag)
                    out.extend([guard + "{"] + lines + ["}"] if guard else lines)
                    del run[:]
            for it in b.items:
                if isinstance(it, str):
                    run.append(it)
                    continue
                flush()
                if it.kind == "el":
                    out += render_el(it, comp_tag, child_mode, last_prev, sub)
                    if it.var != "__none":
                        last_prev = subtree(it)
                elif it.kind == "comp":
                    out += render_comp(it)
                else:
                    out += [l for l in it.items if isinstance(l, str)]
            flush()
            tail = [f'tagNew({b.comp}, __nb{n}, {js(b.tag)}, {main});']
            if b.parent != "-" and b.parent not in self.static_op_parents:
                tail.append(f'propUnder({b.comp}, __nb{n}, (typeof {b.parent} !== "undefined") ? {b.parent} : null);')
            if mode in ("check", "place"):
                tail.append(f'placeNew({b.comp}, __nb{n}, __bl{n});')
            out.extend([guard + "{"] + tail + ["}"] if guard else tail)
            return out

        def render_comp(b):
            head = next((it for it in b.items if isinstance(it, Block) and it.kind == "head"), None)
            if head is None:
                h = own_hash(b)
                manifest[b.tag] = h
                kept = (not full) and old.get(b.tag) == h
                out = []
                if kept:
                    # looked up by tag; if the open project does not have it after all, its own lines run (guarded chunks)
                    self.stats["comps_kept"].append(b.tag)
                    n = nid()
                    out.append(f'var {b.var} = COMPTAG[{js(b.tag)}]; var __mk{n} = !{b.var}; log(({b.var} ? "kept: " : "kept comp missing, recreated: ") + {js(b.tag)});')
                    run = []
                    for it in b.items:
                        if isinstance(it, str):
                            run.append(it)
                            continue
                        if run:
                            out += [f"if (__mk{n}) {{"] + run + ["}"]
                            run = []
                        out += render_comp(it) if it.kind == "comp" else render_el(it, b.tag, "full", [], [])
                    if run:
                        out += [f"if (__mk{n}) {{"] + run + ["}"]
                    return out
                self.stats["comps_rebuilt"].append(b.tag)
                ov = "__old_" + b.var
                out.append(f'var {ov} = COMPTAG[{js(b.tag)}] || null;')
                for it in b.items:
                    out += (render_comp(it) if it.kind == "comp" else render_el(it, b.tag, "full", [], [])) if isinstance(it, Block) else [it]
                out.append(f'if ({ov} && typeof {b.var} !== "undefined" && {b.var} !== {ov}) {{ relinkComp({ov}, {b.var}); {ov}.remove(); }}')
                return out
            hk = b.tag + "#head"
            hh = own_hash(head)
            manifest[hk] = hh
            manifest[b.tag] = hh
            head_kept = (not full) and old.get(hk) == hh
            out = []
            if not head_kept:
                self.stats["comps_rebuilt"].append(b.tag)
                ov = "__old_" + b.var
                out.append(f'var {ov} = COMPTAG[{js(b.tag)}] || null;')
                prev = []
                for it in b.items:
                    if not isinstance(it, Block):
                        out.append(it)
                    elif it.kind == "head":
                        out += [l for l in it.items if isinstance(l, str)]
                    elif it.kind == "el":
                        out += render_el(it, b.tag, "full", prev, [])
                        if it.var != "__none":
                            prev = subtree(it)
                    else:
                        out += render_comp(it)
                out.append(f'if ({ov} && typeof {b.var} !== "undefined" && {b.var} !== {ov}) {{ relinkComp({ov}, {b.var}); {ov}.remove(); }}')
                return out
            self.stats["comps_kept"].append(b.tag)
            out.append(f'var {b.var} = COMPTAG[{js(b.tag)}];')
            out.append(f'if (!{b.var}) {{ log({js("kept comp missing, recreated: " + b.tag)});')
            out += [l for l in head.items if isinstance(l, str)]
            out.append("}")
            new_keys = set(el_keys(b.tag, b))
            stale = [k.split("#", 1)[1] for k in old if k.startswith(b.tag + "#") and k != hk and k not in new_keys]
            if stale:
                self.stats["els_removed"] += len(stale)
                out.append(f'removeTagged({b.var}, {js(stale)}); log({js("removed " + str(len(stale)) + " element(s) of " + b.tag)});')
            prev = []
            for it in b.items:
                if not isinstance(it, Block):
                    out.append(it)
                elif it.kind == "head":
                    continue
                elif it.kind == "el":
                    out += render_el(it, b.tag, "check", prev, [])
                    if it.var != "__none":
                        prev = subtree(it)
                else:
                    out += render_comp(it)
            return out

        out = []
        for it in root.items:
            if isinstance(it, Block):
                out += render_comp(it) if it.kind == "comp" else render_el(it, "root", "full", [], [])
            else:
                out.append(it)
        removals = [t for t in old if "#" not in t and t not in manifest]
        self.stats["comps_removed"] = removals
        if removals:
            k = next((i for i, l in enumerate(out) if 'log("DONE")' in l), len(out))
            out[k:k] = [f'(function () {{ var c = COMPTAG[{js(t)}]; if (c) {{ log({js("removed: " + t)}); c.remove(); }} }})();' for t in removals]
        return "\n".join(out).replace("__MANIFEST__", js(_json.dumps(manifest))), manifest

    def incremental(self, text, old_manifest):
        """(kept for callers of the v1 API) -> render(text, old)"""
        return self.render(text, old_manifest)

    def stats_lines(self):
        st = getattr(self, "stats", None)
        if not st:
            return []
        out = []
        names = self.comp_names
        if self.old_manifest and st["comps_rebuilt"]:
            out.append("rebuilt comps (your AE edits inside them are replaced): " + ", ".join(names.get(t, t) for t in st["comps_rebuilt"]))
        out.append(f'incremental: comps {len(st["comps_kept"])} kept / {len(st["comps_rebuilt"])} rebuilt / {len(st["comps_removed"])} removed ; '
                   f'elements {st["els_kept"]} kept / {st["els_rebuilt"]} rebuilt / {st["els_removed"]} removed')
        return out

    # ------------------------------------------------------------------ top level
    def main_artboard(self):
        if self.main_name:
            for ab in self.p.artboards:
                if ab.name == self.main_name:
                    return ab
            raise SystemExit(f"artboard {self.main_name!r} not found")
        return self.p.default_artboard()

    def convert(self):
        J = self.W
        J(PRELUDE.replace("__LOG__", js(self.log)).replace("__FPS__", js(float(self.fps))))
        J(TIDY_JSX + TIDY_BUILD_JSX)
        J(f"var KEEP = {js(self.keep)};")
        J("try {")
        J("if (!KEEP) {")
        J('  var mine = false; for (var q = 1; q <= app.project.numItems; q++) { var itq = app.project.item(q); if (!itq) continue; var nm = String(itq.name); if (nm == "rml2ae" || nm.indexOf("(rml2ae)") >= 0) mine = true; }')
        # never save a project that is not ours (the user's .aep stays untouched): a clean or empty project is closed
        # as is, one of ours keeps its edits, anything else with unsaved changes stops the build
        J("  if (!app.project.dirty || app.project.numItems == 0) { app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES); }")
        J("  else if (mine && app.project.file) { app.project.close(CloseOptions.SAVE_CHANGES); }")
        J("  else if (mine) { app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES); }")
        J('  else { throw new Error("the open project has unsaved changes and is not an rml2ae project: save or close it first"); }')
        J("  app.newProject();")
        J("}")
        J("try { app.disableRendering = true; } catch (e6) {}")
        J(f'app.beginUndoGroup({js("rml2ae: " + self.p.name)});')
        if self.replace and not self.incremental_mode:
            # rebuild in place: drop the previous import of this project (its folder holds every comp/footage we made)
            J(f'for (var q = app.project.numItems; q >= 1; q--) {{ var it = app.project.item(q); if (it instanceof FolderItem && it.name == {js(self.p.name + " (rml2ae)")}) it.remove(); }}')
        J("var COMPTAG = compTags();")
        J('try { app.project.expressionEngine = "javascript-1.0"; } catch (e) {}')
        J(f'var ROOTF = folder(null, {js(self.p.name + " (rml2ae)")});')
        J('var FOOT = folder(ROOTF, "Footage"); var COMPS = folder(ROOTF, "Artboards"); var SUBS = folder(ROOTF, "Nested");')
        self.import_assets()
        self.build_viewmodels()
        main_ab = self.main_artboard()
        if main_ab is None:
            raise SystemExit("no artboard")
        J(f'log("main artboard {main_ab.name}");')
        self.top_name = self.top_name or main_ab.name
        top = self.artboard_comp(main_ab, top=True)
        if self.top_name is None:
            self.top_name = main_ab.name
        J(f"{top}.parentFolder = ROOTF;")
        J(f'log({js("top " + self.top_name)});')
        if self.all_animations:
            for ab in self.p.artboards:
                for a in ab.findall("LinearAnimation"):
                    self.anim_comp(ab, self.p.animations[a.id], False, suffix=True)
        for (abn, an), els in sorted(self.pruned.items()):
            uniq = list({id(e): e for e in els}.values())
            names = ", ".join(sorted({e.name or e.tag for e in uniq}))
            self.rep.add(abn, "info", None, f"{len(uniq)} element(s) invisible for the whole '{an}' animation (opacity 0 / "
                         f"transparent paint): not built in its comp — {names[:300]}")
        if self.layout == "industry":
            # nulls that do nothing are dissolved (their children keep their world transform; ae pull rebuilds them)
            J('try { var __td = tidyProject(ROOTF); log("tidy: " + __td + " static null(s) dissolved"); } catch (eT) { log("tidy FAILED: " + eT.toString() + " line " + eT.line); }')
        J("app.endUndoGroup();")
        J(f'if (!KEEP) {{ app.project.save(new File({js(self.aep)})); log("saved " + app.project.file.fsName); }}')
        J('else if (app.project.file) { app.project.save(); log("saved " + app.project.file.fsName); }')
        J('log("project " + (app.project.file ? app.project.file.fsName : "unsaved"));')
        # untagged helper comps (FX chains) of rebuilt elements are now orphans: drop them
        J('cleanupUnused(SUBS);')
        # the manifest of what this build produced is written by AE itself, only when the script completed
        J(f'(function () {{ var mf = new File({js(self.manifest_path)}); mf.open("w"); mf.write(__MANIFEST__); mf.close(); }})();')
        J('log("DONE");')
        J('} catch (eTop) { log("TOP FAILED: " + eTop.toString() + " line " + eTop.line); try { app.endUndoGroup(); } catch (e3) {} }')
        J("try { app.disableRendering = false; } catch (e5) {}")
        import json as _json
        self.old_manifest = {}
        if self.incremental_mode and os.path.exists(self.manifest_path):
            try:
                self.old_manifest = _json.load(open(self.manifest_path, encoding="utf-8"))
            except Exception:
                self.old_manifest = {}
        text, self.manifest = self.render(self.W.text(), self.old_manifest, full=not self.incremental_mode)
        try:
            import json as _json2
            with open(os.path.join(self.out_dir, self.p.name + ".groups.json"), "w", encoding="utf-8") as gf:
                _json2.dump({"layout": self.layout, "groups": self.group_map, "folds": self.fold_opacity, "chains": self.chains, "offsets": self.offsets}, gf)
        except OSError:
            pass
        return text

    def import_assets(self):
        J = self.W
        for aid, a in self.p.assets.items():
            f = a.get("file")
            if a.tag == "ImageAsset" and f:
                path = os.path.join(self.p.dir, f)
                if not os.path.exists(path):
                    self.rep.add("assets", "unsupported", a, f"image file missing: {f}")
                    continue
                var = J.var("ft")
                J(f'var {var} = importFile({js(path)}); {var}.parentFolder = FOOT;')
                self.footage[aid] = var
            elif a.tag == "FontAsset" and f:
                path = os.path.join(self.p.dir, f)
                self.fonts[aid] = self.font_info(path, a.name)
            elif a.tag == "AudioAsset" and f:
                path = os.path.join(self.p.dir, f)
                if os.path.exists(path):
                    var = J.var("au")
                    J(f'var {var} = importFile({js(path)}); {var}.parentFolder = FOOT;')
                    self.footage[aid] = var
            elif a.tag == "ScriptAsset":
                self.rep.add("assets", "unsupported", a, f"Luau script {f}: not converted (phase 2)")
            elif a.tag in ("ComponentAsset",):
                pass
            else:
                self.rep.add("assets", "unsupported", a, f"{a.tag} {f or ''}")

    def font_info(self, path, fallback):
        """(PostScript name, ascent/upm, hmtx measure fn) from the TTF; fallback to the asset name."""
        try:
            from fontTools.ttLib import TTFont
            t = TTFont(path)
            ps = t["name"].getDebugName(6) or fallback
            upm = t["head"].unitsPerEm
            asc = t["hhea"].ascent / upm
            desc = -t["hhea"].descent / upm
            cmap, hm = t.getBestCmap(), t["hmtx"]

            def measure(txt, size):
                return sum(hm[cmap.get(ord(c), cmap.get(ord("?"), next(iter(cmap.values()))))][0] for c in txt) / upm * size
            return (ps, asc, desc, measure)
        except Exception as e:
            self.rep.add("assets", "approx", None, f"font {path}: metrics unavailable ({e}); using name {fallback}")
            return (fallback, ASCENT, 0.251, lambda txt, size: len(txt) * size * 0.6)

    # ------------------------------------------------------------------ comps
    def artboard_comp(self, ab, top=False):
        """Comp for an artboard as its default state machine plays it (a chain of animations -> sequence comp)."""
        chain, loops, notes = self.p.entry_chain(ab)
        for n in notes:
            self.rep.add(ab.name, "approx", ab, n)
        if loops:
            self.rep.add(ab.name, "approx", ab, "the state chain loops back: the AE sequence plays once")
        if len(chain) <= 1:
            c = self.anim_comp(ab, chain[0] if chain else None, top)
            if chain and top and chain[0].loop in ("loop", "pingPong") and self.loop_duration > self.duration_of(ab, chain[0]):
                J = self.W
                w, h = ab.num("width", 1920), ab.num("height", 1080)
                mode = "cycle" if chain[0].loop == "loop" else "pingpong"
                lw = J.var("lp")
                tag = f"rive:{ab.id}|looped"
                self.mark(tag, lw)
                # whole cycles (walk-test: 10 s cut its 6.44 s cycle at 55 %), the content comp's background, and the source
                # time as an explicit modulo of the source duration (no loopOut seam on the last key; audit F25/F40)
                cyc = max(1e-6, self.duration_of(ab, chain[0]) * (2 if mode == "pingpong" else 1))
                total = math.ceil(self.loop_duration / cyc - 1e-6) * cyc
                J(f'var {lw} = app.project.items.addComp({js(ab.name + " (looped)")}, {int(round(w))}, {int(round(h))}, 1, {js(total)}, FPS); {lw}.parentFolder = COMPS; {lw}.comment = {js(tag)}; {lw}.bgColor = {c}.bgColor;')
                if mode == "cycle":
                    loop_expr = js("var D = source.duration; var t = time - thisLayer.startTime; t - Math.floor(t / D) * D")
                else:
                    loop_expr = js("var D = source.duration; var t = time - thisLayer.startTime; var u = t - Math.floor(t / (2 * D)) * 2 * D; u < D ? u : 2 * D - u")
                J(f'var ll = {lw}.layers.add({c}); ll.name = {js(ab.name)}; ll.timeRemapEnabled = true; ll.property("ADBE Time Remapping").expression = {loop_expr}; ll.outPoint = {lw}.duration;')
                self.unmark()
                self.rep.add(ab.name, "converted", ab, f"looping animation → comp '{ab.name} (looped)' of {total:g}s "
                             f"({round(total / cyc)} whole {'cycle' if mode == 'cycle' else 'back-and-forth'}{'s' if round(total / cyc) > 1 else ''}, ≥ --duration {self.loop_duration:g}s)")
                c = lw
                if top:
                    self.top_name = ab.name + " (looped)"
            if chain and chain[0].quantize and top:
                # stop-motion at the animation's own fps: a wrapper comp with Posterize Time (the artboard comp stays intact)
                J = self.W
                w, h = ab.num("width", 1920), ab.num("height", 1080)
                wv = J.var("stp")
                tag = f"rive:{ab.id}|stepped"
                self.mark(tag, wv)
                J(f'var {wv} = app.project.items.addComp({js(ab.name + " (stepped)")}, {int(round(w))}, {int(round(h))}, 1, {js(self.duration_of(ab, chain[0]))}, FPS); {wv}.parentFolder = COMPS; {wv}.comment = {js(tag)};')
                J(f'var pl = {wv}.layers.add({c}); pl.name = {js(ab.name)}; var pq = pl.property("ADBE Effect Parade").addProperty("ADBE Posterize Time"); pq.name = "Rive quantize"; pq.property("ADBE Posterize Time-0001").setValue({js(chain[0].fps)});')
                self.unmark()
                self.rep.add(ab.name, "converted", ab, f"quantize → comp '{ab.name} (stepped)' with Posterize Time {chain[0].fps:g}")
                if top:
                    self.top_name = ab.name + " (stepped)"
                return wv
            return c
        if ab.id in self.seq:
            return self.seq[ab.id]
        links = self.p.chain_links(ab)
        fps_set = {a.fps for a in chain}
        if (len(fps_set) == 1 and all(l.get("blend", 0) == 0 and l.get("exit", 1.0) >= 1.0 for l in links[:len(chain) - 1])
                and not any(a.quantize for a in chain) and all((a.speed or 1.0) == 1.0 for a in chain)):
            try:
                merged = ChainAnimation(self.p, ab, chain)
            except Unmergeable as ex:
                self.rep.add(ab.name, "info", ab, f"state chain kept as one comp per state ({ex})")
            else:
                c = self.anim_comp(ab, merged, top)
                self.chains[merged.id] = merged.windows
                self.seq[ab.id] = c
                self.rep.add(ab.name, "converted", ab, f"state chain of {len(chain)} animations → ONE comp, keys shifted "
                                                       f"(" + ", ".join(a.name for a in chain) + ")")
                return c
        J = self.W
        var = J.var("seq")
        total = sum(self.duration_of(ab, a) for a in chain)
        tag = f"rive:{ab.id}|seq"
        self.mark(tag, var, ab.name)
        J(f'var {var} = app.project.items.addComp({js(ab.name)}, {int(round(ab.num("width", 1920)))}, {int(round(ab.num("height", 1080)))}, 1, {js(total)}, FPS); {var}.parentFolder = {"COMPS" if top else "SUBS"}; {var}.comment = {js(tag)};')
        t = 0.0
        carry = {}                    # a state machine keeps the last value of what the previous state keyed
        links = self.p.chain_links(ab)
        for i, a in enumerate(chain):
            c = self.anim_comp(ab, a, False, suffix=True, carry=dict(carry))
            for (oid, pk), lst in a.keys.items():
                if lst:
                    v = lst[-1][1]
                    carry[(oid, prop_name(pk))] = tuple(v) if isinstance(v, list) else v
            lv = J.var("sl")
            d = self.duration_of(ab, a)
            link = links[i] if i < len(links) else {"exit": 1.0, "blend": 0.0}
            blend = link["blend"] if i + 1 < len(chain) else 0.0
            end = t + d * link["exit"]
            J(f'var {lv} = {var}.layers.add({c}); {lv}.name = {js(a.name)}; {lv}.startTime = {js(t)}; {lv}.outPoint = {js(min(t + d, end + blend))};')
            if i > 0 and links[i - 1]["blend"] > 0:
                b0 = links[i - 1]["blend"]
                J(f'keys(tr({lv}, "ADBE Opacity"), {js([[t * self.fps, 0, "l", 0, 0, 0, 0], [(t + b0) * self.fps, 100, "h", 0, 0, 0, 0]])});')
                self.rep.add(ab.name, "approx", ab, f"transition into '{a.name}' blends over {b0:.2f}s: opacity crossfade in AE (Rive mixes property values)")
            if link["exit"] < 1.0:
                self.rep.add(ab.name, "converted", ab, f"'{a.name}' exits at {link['exit'] * 100:.0f}% of its duration")
            if a.quantize:
                J(f'var pq = {lv}.property("ADBE Effect Parade").addProperty("ADBE Posterize Time"); pq.name = "Rive quantize"; pq.property("ADBE Posterize Time-0001").setValue({js(a.fps)});')
            t = end
        self.seq[ab.id] = var
        self.unmark()
        self.rep.add(ab.name, "converted", ab, f"sequence of {len(chain)} animations: " + ", ".join(a.name for a in chain))
        return var

    def duration_of(self, ab, anim, depth=0):
        """Seconds an (artboard, animation) comp must last: its animation, or the longest nested content it shows."""
        key = (ab.id, anim.id if anim else None)
        if key in self.durs:
            return self.durs[key]
        dur = (anim.duration / anim.fps / (anim.speed or 1.0)) if anim else 0.0
        self.durs[key] = dur          # guards recursion
        if depth < 8:
            for el in ab.iter():
                sub, sub_anim = None, None
                if el.tag == "NestedArtboard":
                    sub = self.p.by_id.get(el.get("artboardId"))
                    r = el.find("NestedRemapAnimation") or el.find("NestedSimpleAnimation")
                    if r is not None:
                        sub_anim = self.p.animations.get(r.get("animationId"))
                        d = self.duration_of(sub, sub_anim, depth + 1) if sub is not None else 0
                        if el.find("NestedRemapAnimation") is not None:
                            d = 0     # scrubbed: the parent's own keys decide
                    elif sub is not None:
                        d = self.chain_duration(sub, depth + 1)
                    else:
                        d = 0
                    dur = max(dur, d)
                elif el.tag == "ScriptInputArtboard":
                    sub = self.p.by_id.get(el.get("artboardId"))
                    if sub is not None:
                        dur = max(dur, self.chain_duration(sub, depth + 1))
        self.durs[key] = max(dur, 1 / self.fps)
        return self.durs[key]

    def chain_duration(self, ab, depth=0):
        chain, _, _ = self.p.entry_chain(ab)
        if not chain:
            return self.duration_of(ab, None, depth)
        return sum(self.duration_of(ab, a, depth) for a in chain)

    def anim_comp(self, ab, anim, top=False, suffix=False, carry=None):
        carry = carry or {}
        key = (ab.id, anim.id if anim else None, tuple(sorted(carry.items())))
        if key in self.comps:
            return self.comps[key]
        J = self.W
        var = J.var("c")
        self.comps[key] = var
        w, h = ab.num("width", 1920), ab.num("height", 1080)
        dur = self.duration_of(ab, anim)
        name = ab.name + (f" · {anim.name}" if (suffix and anim) else "")
        self.mark("rive:" + (ab.id or "") + "|" + (anim.id if anim else ""), var, name)
        J(f"// ================================================================== {name}")
        J(f"//<head {var}>")
        J("try {")
        J(f'var {var} = app.project.items.addComp({js(name)}, {int(round(w))}, {int(round(h))}, 1, {js(max(dur, 1 / self.fps))}, FPS); {var}.parentFolder = {"COMPS" if top else "SUBS"};')
        J(f'{var}.comment = {js("rive:" + (ab.id or "") + "|" + (anim.id if anim else ""))};')
        if top:
            J(f"selfTest({var});")
        ctx = Ctx(self, var, ab, anim, carry)
        # background fill
        bg = ab.find("Fill")
        if bg is not None and self.gradient_of(bg) is not None:
            J(f'var bg = {var}.layers.addSolid([1, 1, 1], "Background", {int(w)}, {int(h)}, 1); bg.name = "Background"; resetTf(bg, 0, 0, 0, 0);')
            self.emit_gradient("bg", self.gradient_of(bg), ctx, bg)
            self.emit_binds(bg, {"isVisible": ('tr(bg, "ADBE Opacity")', "%s"), "opacity": ('tr(bg, "ADBE Opacity")', "%s * 100")}, ctx)
        elif bg is not None:
            sc = bg.find("SolidColor")
            if sc is not None:
                r, g, b, a = argb(sc.get("colorValue"))
                J(f"{var}.bgColor = {js([r, g, b])};")
                if a > 0:
                    J(f'var bg = {var}.layers.addSolid({js([r, g, b])}, "Background", {int(w)}, {int(h)}, 1); bg.name = "Background"; tr(bg, "ADBE Opacity").setValue({js(a * 100)});')
                    # the artboard fill can be data-bound (isVisible / opacity), like any paint
                    self.emit_binds(bg, {"isVisible": ('tr(bg, "ADBE Opacity")', "%s"), "opacity": ('tr(bg, "ADBE Opacity")', "%s * 100")}, ctx)
        if anim and anim.unsupported:
            for oid, pk, other in anim.unsupported[:20]:
                self.rep.add(ab.name, "approx", self.p.by_id.get(oid), f"{other} on {prop_name(pk)}: interpolated linearly")
        J(f'tagNew({var}, 0, "head", null);')
        J(f'}} catch (eC) {{ log({js("comp FAILED: " + name)} + ": " + eC.toString() + " line " + eC.line); }}')
        J("//</head>")
        self.emit_children(ab, None, ctx)
        J(f"//<el audio:{ab.id} {var} ->")
        av = self.emit_audio(ab, ctx)
        J(f"//</el audio:{ab.id} {av or '__none'}>")
        J(f'log({js("comp ok: " + name)});')
        self.unmark()
        return var

    # ------------------------------------------------------------------ elements
    def op_varies(self, el, ctx):
        """the opacity of `el` or of one of its ancestors changes or differs from 1 in this comp (keys, a binding, a
        static value): only then do the children's layers need the propagation expression (AE parenting does not
        carry opacity; Rive multiplies it down). Audit F11: 75 % of them multiplied by a parent always at 100 %."""
        cur = el
        while cur is not None and cur.tag != "Artboard":
            if cur.id and (abs(cur.num("opacity", 1) - 1) > 1e-9 or ctx.keys(cur.id, "opacity")):
                return True
            if any(c.tag == "DataBindContext" and prop_name(c.get("propertyKey", -1)) == "opacity" for c in cur.children):
                return True
            node = self._fold.get(cur.id) if cur.id else None
            if node is not None and node is not cur and self.op_varies(node, ctx):
                return True
            cur = cur.parent
        return False

    @staticmethod
    def _visible_spans(keys, dur):
        """frames where an opacity key list is above 0, as merged [a, b) spans over [0, dur]: a hold segment is
        invisible when its key is 0, any other segment when both of its ends are 0 (Rive interpolates the value)"""
        spans = []
        pts = [(f, abs(float(v)) > 1e-6, i) for f, v, i, _e in keys]
        if pts[0][0] > 0:
            if pts[0][1]:
                spans.append([0, pts[0][0]])
        for k, (f, vis, interp) in enumerate(pts):
            nxt = pts[k + 1] if k + 1 < len(pts) else None
            end = nxt[0] if nxt else dur
            if end <= f:
                continue
            if vis or (nxt is not None and interp != "hold" and nxt[1]):
                spans.append([f, end])
        out = []
        for a, b in spans:
            if out and a <= out[-1][1] + 1e-9:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    def visible_window(self, els, ctx):
        """(t0, t1) in comp seconds: the hull of the frames where any of `els` can be seen (their opacity and their
        ancestors' above 0), or None when it is the whole animation / unknown (a binding drives an opacity)"""
        if not ctx.anim:
            return None
        dur = float(ctx.anim.duration)
        lo, hi = None, None
        for el in els:
            spans = [[0.0, dur]]
            cur = el
            while cur is not None and cur.tag != "Artboard":
                if any(c.tag == "DataBindContext" and prop_name(c.get("propertyKey", -1)) in ("opacity", "isVisible")
                       for c in cur.children):
                    return None
                ks = ctx.keys(cur.id, "opacity") if cur.id else None
                if ks:
                    own = self._visible_spans(ks, dur)
                    spans = [[max(a, c), min(b, d)] for a, b in spans for c, d in own if min(b, d) > max(a, c)]
                    if not spans:
                        return None            # never visible: _dead's business, not a cut
                cur = cur.parent
            a, b = spans[0][0], spans[-1][1]
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
        if lo is None or (lo <= 1e-9 and hi >= dur - 1e-9):
            return None
        return (lo * ctx.fscale / self.fps, hi * ctx.fscale / self.fps)

    def trim(self, var, els, ctx):
        """cut the layer to the window where its element(s) can be seen"""
        if not isinstance(var, str) or self.layout != "industry":
            return
        w = self.visible_window(els, ctx)
        if w is not None:
            self.W(f"trimTo({var}, {js(w[0])}, {js(w[1])});")

    # ---- elements that draw nothing in a comp are not built (audit F2: 58 % of film-test's layers were slots hidden for
    # the whole board, rims at opacity 0, clip sources drawn with a transparent fill)
    def _rig_targets(self, ab):
        """ids another element needs to exist in AE: constraint / IK / follow-path targets, skin bones"""
        key = ab.id
        if key not in self._rig_cache:
            ids = set()
            for e in ab.iter():
                for k in ("targetId", "boneId"):
                    if e.get(k):
                        ids.add(e.get(k))
            self._rig_cache[key] = ids
        return self._rig_cache[key]

    def _has_audio(self, ab, seen=None):
        seen = seen or set()
        if ab is None or ab.id in seen:
            return False
        seen.add(ab.id)
        for e in ab.iter():
            if e.tag == "AudioEvent" or (e.tag == "NestedArtboard" and self._has_audio(self.p.by_id.get(e.get("artboardId")), seen)):
                return True
        return False

    def _paint_transparent(self, p, ctx):
        if self.gradient_of(p) is not None or p.find("Feather") is not None:
            return False
        if any(e.tag == "DataBindContext" for e in p.iter()):
            return False
        sc = p.find("SolidColor")
        if sc is None or ctx.keys(sc.id, "colorValue"):
            return False
        return argb(sc.get("colorValue"))[3] <= 1e-6

    def _dead(self, el, ctx):
        """`el` draws nothing for the whole comp: opacity 0 throughout (keys including what the previous state left,
        no binding), a shape whose paints are all transparent, or a container whose elements are all dead"""
        if el.id and el.id in self._rig_targets(ctx.artboard):
            return False
        if el.ae or el.tag in ("ScriptedDrawable", "ScriptedLayout") or is_a(el.tag, "Bone") or self.needs_replay(el):
            return False
        if any(c.tag == "DataBindContext" and prop_name(c.get("propertyKey", -1)) in ("opacity", "isVisible")
               for c in el.children):
            return False
        if el.tag == "NestedArtboard" and self._has_audio(self.p.by_id.get(el.get("artboardId"))):
            return False                  # Rive still plays a hidden nested artboard's sounds
        ks = ctx.keys(el.id, "opacity") if el.id else None
        if ks:
            if all(abs(float(v)) < 1e-6 for _f, v, _i, _e in ks):
                return True
        elif el.get("opacity") is not None and abs(el.num("opacity", 1)) < 1e-6:
            return True
        if is_a(el.tag, "Shape"):
            paints = [c for c in el.children if c.tag in ("Fill", "Stroke")]
            if paints and all(self._paint_transparent(p, ctx) for p in paints):
                return True
            return False
        if is_a(el.tag, "Node") or el.tag == "Solo" or is_a(el.tag, "LayoutComponent"):
            kids = [c for c in el.children if self.is_element(c)]
            return bool(kids) and all(self._dead(c, ctx) for c in kids)
        return False

    def emit_children(self, container, parent, ctx):
        """Children of a container, bottom first (Rive draws the first child on top)."""
        if parent and not self.op_varies(container, ctx):
            self.static_op_parents.add(parent)        # its children need no opacity-propagation expression
        drawables = [c for c in container.children if self.is_element(c)]
        if container.tag != "Solo":
            dead = [c for c in drawables if self._dead(c, ctx)]
            if dead:
                self.pruned.setdefault((ctx.artboard.name, ctx.anim.name if ctx.anim else ""), []).extend(dead)
                drawables = [c for c in drawables if c not in dead]
        kids = self.solo_kids.get(container.id) if container.tag == "Solo" else None
        if self.layout == "industry" and not kids:
            return self._emit_children_grouped(container, drawables, parent, ctx)
        for i, c in enumerate(reversed(drawables)):
            tag = self.tag_of(c)
            self.W(f"//<el {tag} {ctx.comp} {parent or '-'}>")
            v = self.emit_element(c, parent, ctx)
            if c.ae:
                self.emit_effects(c, v, ctx)
            if kids and isinstance(v, str) and c.id in kids:
                solo_expr = 'value * (thisLayer.parent.effect("Solo active")("ADBE Slider Control-0001") == %d ? 1 : 0)' % kids.index(c.id)
                self.W(f'tr({v}, "ADBE Opacity").expression = {js(solo_expr)};')
            self.W(f"//</el {tag} {v if isinstance(v, str) else '__none'}>")

    # ---- industry layout: how a motion designer would build it — one shape layer per group of shapes (each Rive
    # Shape / Node a vector group with its own transform and keys), no null for a node that does nothing
    def _shape_groupable(self, el):
        """a Shape that can live as a vector group inside a shared shape layer"""
        if not is_a(el.tag, "Shape") or el.ae or self.needs_replay(el) or RIG.has_skin(el):
            return False
        if (el.get("blendModeValue") or "srcOver") != "srcOver" or int(el.num("drawableFlags", 0)) & 1:
            return False
        for c in el.children:
            if c.tag in ("Fill", "Stroke"):
                if self.gradient_of(c) is not None or c.find("Feather") is not None:
                    return False          # layer-level: Gradient Ramp effect / blur
            elif not is_a(c.tag, "Path"):
                return False              # clip, bind, nested node…
        return any(c.tag in ("Fill", "Stroke") for c in el.children)

    def _matte_groupable(self, el):
        """a clip source Node: only nodes and shapes made of paths (paints do not matter, Rive clips by geometry)"""
        if el.tag == "Node":
            if el.find("ClippingShape") is not None or self.needs_replay(el):
                return False
            kids = [c for c in el.children if self.is_element(c)]
            return bool(kids) and all(self._matte_groupable(c) for c in kids)
        if is_a(el.tag, "Shape"):
            if RIG.has_skin(el) or self.needs_replay(el):
                return False
            return any(is_a(c.tag, "Path") for c in el.children)
        return False

    def _node_groupable(self, el, root=False):
        """a plain Node whose whole subtree can be one shape layer (the root may carry bindings: they go on the layer)"""
        if el.tag != "Node" or el.ae or self.needs_replay(el) or el.find("ClippingShape") is not None:
            return False
        if (el.get("blendModeValue") or "srcOver") != "srcOver" or int(el.num("drawableFlags", 0)) & 1:
            return False
        if not root and any(c.tag == "DataBindContext" for c in el.children):
            return False
        kids = [c for c in el.children if self.is_element(c)]
        if not kids or any(c.tag not in ("Node",) and not is_a(c.tag, "Shape") for c in el.children
                           if c.tag not in ("DataBindContext",) and self.is_element(c)):
            return False
        return all(self._shape_groupable(c) if is_a(c.tag, "Shape") else self._node_groupable(c) for c in kids)

    def _moves(self, el):
        """x or y keyed in some animation: on its own layer, position is separated in X / Y (exact eases both ways);
        a vector group's position is one 2-D property whose eases ae pull cannot split back (measured: 79 false edits)"""
        for a in self.p.animations.values():
            for (o, pk) in a.keys:
                if o == el.id and prop_name(pk) in ("x", "y"):
                    return True
        return False

    def _identity_node(self, el):
        """a Node that changes nothing (no transform, no keys, no clip, no binding): no null for it in AE"""
        if el.tag != "Node" or el.ae or el.find("ClippingShape") is not None:
            return False
        if any(c.tag == "DataBindContext" for c in el.children) or int(el.num("drawableFlags", 0)) & 1:
            return False
        if (el.get("blendModeValue") or "srcOver") != "srcOver":
            return False
        ident = (abs(el.num("x")) < 1e-9 and abs(el.num("y")) < 1e-9 and abs(el.num("rotation")) < 1e-9 and
                 abs(el.num("scaleX", 1) - 1) < 1e-9 and abs(el.num("scaleY", 1) - 1) < 1e-9 and abs(el.num("opacity", 1) - 1) < 1e-9)
        if not ident:
            return False
        keyed = getattr(self, "_keyed_ids", None)
        if keyed is None:
            keyed = self._keyed_ids = {o for a in self.p.animations.values() for (o, _pk) in a.keys}
        return el.id not in keyed

    def _foldable(self, el):
        """a Node holding ONE static leaf (shape / text / image, no rotation or scale of its own): the leaf's layer takes
        the node's transform and keys, its offset going into the anchor point — no null (T(p)R S T(c) = a layer at p
        with anchor −c, exact)"""
        if el.tag != "Node" or el.ae or el.find("ClippingShape") is not None or int(el.num("drawableFlags", 0)) & 1:
            return None
        if (el.get("blendModeValue") or "srcOver") != "srcOver":
            return None
        kids = [c for c in el.children if self.is_element(c)]
        if len(kids) != 1:
            return None
        c = kids[0]
        # a nested artboard too (a film's shot: its holder node carried the cut, now the precomp layer does)
        if not (is_a(c.tag, "Shape") or is_a(c.tag, "Text") or is_a(c.tag, "Image") or c.tag == "NestedArtboard") or c.ae or self.needs_replay(c):
            return None
        if is_a(c.tag, "Shape") and (RIG.has_skin(c) or any(self.gradient_of(x) is not None for x in c.children if x.tag in ("Fill", "Stroke"))):
            return None
        if any(x.tag == "DataBindContext" for x in c.children) or c.find("ClippingShape") is not None:
            return None
        if abs(c.num("rotation")) > 1e-9 or abs(c.num("scaleX", 1) - 1) > 1e-9 or abs(c.num("scaleY", 1) - 1) > 1e-9:
            return None
        for a in self.p.animations.values():
            for (o, pk) in a.keys:
                if o == c.id and prop_name(pk) in ("x", "y", "rotation", "scaleX", "scaleY", "opacity"):
                    return None
        return c

    def _absorbable(self, el, ctx):
        """a Node that only groups ONE child, with a static translation and/or opacity bindings (film-test's 'Set',
        'photo pass', 'text pass'): no null — the child's layer takes the translation in its position (values, keys
        and bindings) and the node's opacity bindings chained on its own opacity (exact: no rotation / scale above)."""
        if el.tag != "Node" or el.ae or el.find("ClippingShape") is not None or int(el.num("drawableFlags", 0)) & 1:
            return None
        if (el.get("blendModeValue") or "srcOver") != "srcOver":
            return None
        if abs(el.num("rotation")) > 1e-9 or abs(el.num("scaleX", 1) - 1) > 1e-9 or abs(el.num("scaleY", 1) - 1) > 1e-9:
            return None
        keyed = getattr(self, "_keyed_ids", None)
        if keyed is None:
            keyed = self._keyed_ids = {o for a in self.p.animations.values() for (o, _pk) in a.keys}
        if el.id in keyed:
            return None
        if any(prop not in ("opacity", "isVisible") for prop, _d in self.binds_quiet(el)):
            return None
        if el.id and el.id in self._rig_targets(ctx.artboard):
            return None
        kids = [c for c in el.children if self.is_element(c)]
        if len(kids) != 1:
            return None
        c = kids[0]
        if c.tag not in ("Node",) and not (is_a(c.tag, "Shape") or is_a(c.tag, "Text") or is_a(c.tag, "Image")):
            return None
        if c.ae or self.needs_replay(c) or c.find("ClippingShape") is not None or (is_a(c.tag, "Shape") and RIG.has_skin(c)):
            return None
        if is_a(c.tag, "Image") and c.find("Mesh") is not None:
            return None                           # emit_mesh places corner-pinned rows itself (no base_transform)
        return c

    def binds_quiet(self, el):
        """(property name, vm prop) of an element's bindings, without reporting anything"""
        out = []
        for b in el.children:
            if b.tag == "DataBindContext" and b.get("propertyKey"):
                out.append((prop_name(b.get("propertyKey")), None))
        return out

    def _artboard_of(self, el):
        while el is not None and el.tag != "Artboard":
            el = el.parent
        return el

    def _emit_children_grouped(self, container, drawables, parent, ctx):
        """bottom first; a run of consecutive groupable siblings becomes one shape layer, an identity node dissolves
        (its children take its place, parented to its parent)"""
        items, run = [], []
        for c in drawables:                      # Rive order: first = on top
            # loose sibling shapes share a layer, and so do sibling groups made only of shapes (a vector group each,
            # with its own transform and keys): a 72 s film had 705 such one-group layers stacked side by side
            if self._shape_groupable(c) or (self._node_groupable(c) and not self._moves(c)):
                run.append(c)
                continue
            if run:
                items.append(run)
                run = []
            items.append(c)
        if run:
            items.append(run)
        for it in reversed(items):
            if isinstance(it, list):
                if len(it) == 1:
                    self._emit_one(it[0], parent, ctx)
                else:
                    tag = self.tag_of(it[0]) + "+run"
                    self.W(f"//<el {tag} {ctx.comp} {parent or '-'}>")
                    v = self.emit_shape_run(container, it, parent, ctx, tag)
                    self.trim(v, it, ctx)
                    self.W(f"//</el {tag} {v}>")
                continue
            if self._identity_node(it) and not self._node_groupable(it, root=True):
                self.rep.add(ctx.artboard.name, "converted", it, "identity node: no null (its layers sit under its parent)")
                self.emit_children(it, parent, ctx)
                continue
            self._emit_item(it, parent, ctx)

    def _emit_item(self, it, parent, ctx):
        """one non-run item of _emit_children_grouped (a dissolved node hands its child back here)"""
        if it.tag == "Node":
            c = self._absorbable(it, ctx) if not self._node_groupable(it, root=True) and self._foldable(it) is None else None
            if c is not None:
                if self._dead(c, ctx):
                    self.pruned.setdefault((ctx.artboard.name, ctx.anim.name if ctx.anim else ""), []).append(c)
                    return
                dx, dy, hosts = self._absorbed.get(id(it), (0.0, 0.0, []))
                self._absorbed[id(c)] = (dx + it.num("x"), dy + it.num("y"), hosts + [it])   # by object: anonymous nodes too
                self.rep.add(ctx.artboard.name, "converted", it, f"node grouping one {c.tag}: no null (translation and opacity bindings carried by '{c.name}')")
                self._emit_item(c, parent, ctx)
                return
        leaf = self._foldable(it) if it.tag == "Node" and not self._node_groupable(it, root=True) else None
        if leaf is not None:
            self._fold[leaf.id] = it
            tag = self.tag_of(it)                 # the layer stands for the NODE (ae pull writes the node back)
            self.W(f"//<el {tag} {ctx.comp} {parent or '-'}>")
            v = self.emit_element(leaf, parent, ctx)
            if leaf.ae:
                self.emit_effects(leaf, v, ctx)
            self.trim(v, [leaf], ctx)
            self.W(f"//</el {tag} {v if isinstance(v, str) else '__none'}>")
            self.rep.add(ctx.artboard.name, "converted", it, f"node holding one {leaf.tag}: no null, the layer carries its animation")
            return
        self._emit_one(it, parent, ctx)

    def _emit_one(self, c, parent, ctx):
        tag = self.tag_of(c)
        self.W(f"//<el {tag} {ctx.comp} {parent or '-'}>")
        v = self.emit_element(c, parent, ctx)
        if c.ae:
            self.emit_effects(c, v, ctx)
        self.trim(v, [c], ctx)
        self.W(f"//</el {tag} {v if isinstance(v, str) else '__none'}>")

    def emit_merged(self, el, parent, ctx):
        """a Node whose subtree is only shapes -> ONE shape layer carrying the node's transform; its children are
        vector groups (a Node a group of groups, a Shape a group of paths + paints), each with its own keys"""
        J = self.W
        var = J.var("shp")
        J(f'var {var} = {ctx.comp}.layers.addShape(); {var}.name = {js(el.name)};')
        if parent:
            J(f"{var}.parent = {parent};")
        J(f'var gr = {var}.property("ADBE Root Vectors Group");')
        n = self._emit_groups([c for c in el.children if self.is_element(c)], "gr", ctx, self.tag_of(el))
        J(f'tr({var}, "ADBE Anchor Point").setValue([0, 0]);')
        self.base_transform(var, el, ctx)
        self.rep.add(ctx.artboard.name, "converted", el, f"node of {n} shapes → one shape layer (vector groups)")
        return var

    def emit_merged_matte(self, src, parent, ctx):
        """a clip source that is a Node of shapes -> ONE shape layer (vector groups), every path filled opaque: Rive clips
        by the geometry of all the node's shapes, whatever their paint"""
        J = self.W
        var = J.var("shp")
        J(f'var {var} = {ctx.comp}.layers.addShape(); {var}.name = {js(src.name + " (matte)")};')
        if parent:
            J(f"{var}.parent = {parent};")
        J(f'var gr = {var}.property("ADBE Root Vectors Group");')
        self._emit_groups([c for c in src.children if self.is_element(c)], "gr", ctx, "rive:" + (src.id or "") + "+mattegroups",
                          matte=True)
        J(f'tr({var}, "ADBE Anchor Point").setValue([0, 0]);')
        self.base_transform(var, src, ctx)
        return var

    @staticmethod
    def _local_matrix(e):
        x, y, r = e.num("x"), e.num("y"), e.num("rotation")
        sx, sy = e.num("scaleX", 1), e.num("scaleY", 1)
        c, s_ = math.cos(r), math.sin(r)
        return [[c * sx, -s_ * sy, x], [s_ * sx, c * sy, y], [0.0, 0.0, 1.0]]

    def matte_space(self, src, target, host, ctx, ab):
        """the AE parent for a clip source's matte: `host` (the layer standing for `target`) when the source's parent IS
        target; else nulls under host that rebuild the source's own space: the static inverse of target's branch up to
        the common ancestor, then one null per node of the source's branch (with its keys: a card's burn zone shrinks)"""
        J = self.W
        sp = src.parent
        if sp is None or sp is target or target is None:
            return host

        def chain(e):
            out = []
            while e is not None and e.tag != "Artboard":
                out.append(e)
                e = e.parent
            return out
        up_s, up_t = chain(sp), chain(target)
        ca = next((e for e in up_s if any(e is t for t in up_t)), None)
        below = lambda lst: [e for e in lst[:next((k for k, e in enumerate(lst) if e is ca), len(lst))]]
        br_s, br_t = below(up_s), below(up_t)            # bottom-up, the common ancestor excluded
        keyed = getattr(self, "_keyed_ids", None)
        if keyed is None:
            keyed = self._keyed_ids = {o for a in self.p.animations.values() for (o, _pk) in a.keys}
        if any(e.id in keyed for e in br_t):
            self.rep.add(ab.name, "approx", src, "clip source in another branch, the clipped node's own branch is animated: "
                                                 "its inverse is static")
        mt = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        for e in br_t:                                    # target space -> common ancestor space
            lm = self._local_matrix(e)
            mt = [[sum(lm[i][k] * mt[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
        cur = host
        det = mt[0][0] * mt[1][1] - mt[0][1] * mt[1][0]
        ident = abs(mt[0][0] - 1) + abs(mt[1][1] - 1) + abs(mt[0][1]) + abs(mt[1][0]) + abs(mt[0][2]) + abs(mt[1][2]) < 1e-9
        if not ident and abs(det) > 1e-12:
            inv = [[mt[1][1] / det, -mt[0][1] / det, 0.0], [-mt[1][0] / det, mt[0][0] / det, 0.0], [0.0, 0.0, 1.0]]
            inv[0][2] = -(inv[0][0] * mt[0][2] + inv[0][1] * mt[1][2])
            inv[1][2] = -(inv[1][0] * mt[0][2] + inv[1][1] * mt[1][2])
            rot = math.degrees(math.atan2(inv[1][0], inv[0][0]))
            sx = math.hypot(inv[0][0], inv[1][0])
            sy = (inv[0][0] * inv[1][1] - inv[0][1] * inv[1][0]) / (sx or 1)
            off = J.var("nul")
            J(f'var {off} = {ctx.comp}.layers.addNull(); {off}.name = {js(src.name + " (matte space)")}; {off}.label = 3; {off}.enabled = false;')
            if cur:
                J(f"{off}.parent = {cur};")
            J(f'tr({off}, "ADBE Anchor Point").setValue([0, 0]); tr({off}, "ADBE Position").setValue([{js(inv[0][2])}, {js(inv[1][2])}]); '
              f'tr({off}, "ADBE Rotate Z").setValue({js(rot)}); tr({off}, "ADBE Scale").setValue([{js(sx * 100)}, {js(sy * 100)}]);')
            cur = off
        for e in reversed(br_s):                          # common ancestor -> the source's parent, keys included
            if e.id not in keyed and self._identity_node(e):
                continue
            nv = J.var("nul")
            J(f'var {nv} = {ctx.comp}.layers.addNull(); {nv}.name = {js(e.name + " (matte space)")}; {nv}.label = 3; {nv}.enabled = false;')
            if cur:
                J(f"{nv}.parent = {cur};")
            J(f'tr({nv}, "ADBE Anchor Point").setValue([0, 0]);')
            self.base_transform(nv, e, ctx)
            cur = nv
        return cur

    def emit_shape_run(self, container, run, parent, ctx, tag):
        """consecutive groupable siblings -> one shape layer at identity, a vector group each"""
        J = self.W
        var = J.var("shp")
        names = [c.name for c in run]
        name = f"{container.name} · {names[0]}…{names[-1]}" if container.tag != "Artboard" else f"{names[0]}…{names[-1]}"
        J(f'var {var} = {ctx.comp}.layers.addShape(); {var}.name = {js(name)};')
        if parent:
            J(f"{var}.parent = {parent};")
        J(f'var gr = {var}.property("ADBE Root Vectors Group");')
        n = self._emit_groups(run, "gr", ctx, tag)
        J(f'resetTf({var}, 0, 0, 0, 0); {var}.comment = {js(tag)};')
        self.rep.add(ctx.artboard.name, "converted", run[0], f"{len(run)} sibling shapes/groups ({n} shapes) → one shape layer")
        return var

    def _emit_groups(self, els, cvar, ctx, layer_tag, path=(), matte=False):
        """els in Rive order (first on top) -> vector groups added in that order (AE: first in the list on top).
        Each group's index path inside its layer is recorded (group_map): `ae pull` reads the groups back by it."""
        J = self.W
        n = 0
        for i, c in enumerate(els):
            gpath = list(path) + [i + 1]
            if c.id:
                self.group_map.setdefault(layer_tag, []).append([gpath, c.id])
            gv = J.var("grp")
            J(f'var {gv} = {cvar}.addProperty("ADBE Vector Group"); {gv}.name = {js(c.name)};')
            self._group_transform(gv, c, ctx)
            if is_a(c.tag, "Shape"):
                J(f'var gc = {gv}.property("ADBE Vectors Group");')
                for p in c.children:
                    if is_a(p.tag, "Path"):
                        self.emit_path(p, ctx, c)
                if matte:           # a clip: the geometry, filled opaque
                    J('var fl = gc.addProperty("ADBE Vector Graphic - Fill"); fl.property("ADBE Vector Fill Color").setValue([1,1,1]); fl.property("ADBE Vector Fill Opacity").setValue(100);')
                else:
                    for p in reversed(c.children):
                        if p.tag == "Fill":
                            self.emit_fill(p, ctx, c, False)
                        elif p.tag == "Stroke":
                            self.emit_stroke(p, ctx, c)
                n += 1
            else:
                n += self._emit_groups([x for x in c.children if self.is_element(x)], f'{gv}.property("ADBE Vectors Group")', ctx,
                                       layer_tag, gpath, matte)
        return n

    def _group_transform(self, gv, el, ctx):
        """a Rive Node / Shape transform on an AE vector group (position is one 2-D property there)"""
        J = self.W
        t = f'{gv}.property("ADBE Vector Transform Group")'
        x, y = el.num("x"), el.num("y")
        rot = el.num("rotation") * 180 / math.pi
        sx, sy = el.num("scaleX", 1), el.num("scaleY", 1)
        op = el.num("opacity", 1)
        J(f'{t}.property("ADBE Vector Position").setValue([{js(x)}, {js(y)}]); {t}.property("ADBE Vector Rotation").setValue({js(rot)}); {t}.property("ADBE Vector Scale").setValue([{js(sx * 100)}, {js(sy * 100)}]);')
        if op != 1:
            J(f'{t}.property("ADBE Vector Group Opacity").setValue({js(op * 100)});')
        kx, ky, kr = ctx.keys(el.id, "x"), ctx.keys(el.id, "y"), ctx.keys(el.id, "rotation")
        ksx, ksy, ko = ctx.keys(el.id, "scaleX"), ctx.keys(el.id, "scaleY"), ctx.keys(el.id, "opacity")
        if kx or ky:
            J(f'keys({t}.property("ADBE Vector Position"), {self._rows_2d(ctx, kx, ky, x, y, 1.0)});')
        if kr:
            J(f'keys({t}.property("ADBE Vector Rotation"), {ctx.rows(kr, lambda v: v * 180 / math.pi)});')
        if ksx or ksy:
            J(f'keys({t}.property("ADBE Vector Scale"), {self._rows_2d(ctx, ksx, ksy, sx, sy, 100.0)});')
        if ko:
            J(f'keys({t}.property("ADBE Vector Group Opacity"), {ctx.rows(ko, lambda v: v * 100)});')

    @staticmethod
    def _rows_2d(ctx, la, lb, a0, b0, k):
        """two 1-D Rive key lists -> one 2-D AE list: exact when they share frames and eases, else baked per frame"""
        la = la or [(0, a0, "hold", None)]
        lb = lb or [(0, b0, "hold", None)]
        same = [(f, i, e) for f, _v, i, e in la] == [(f, i, e) for f, _v, i, e in lb]
        if same or len(la) == 1 or len(lb) == 1:
            if len(la) == 1 and len(lb) > 1:
                return ctx.rows2(lb, la, lambda b, a: [a * k, b * k])
            return ctx.rows2(la, lb, lambda a, b: [a * k, b * k])
        f0 = int(min(la[0][0], lb[0][0]))
        f1 = int(math.ceil(max(la[-1][0], lb[-1][0])))
        out = [[f * ctx.fscale, [value_at(la, f) * k, value_at(lb, f) * k], "l", 0, 0, 0, 0] for f in range(f0, f1 + 1)]
        return js(out)

    def tag_of(self, el):
        """the tag every layer of an element carries in its `comment`"""
        return "rive:" + el.id if el.id else "anon:" + self.path_of(el)

    def emit_effects(self, el, var, ctx):
        """<!-- ae: … --> comments before the element -> AE effects on its main layer"""
        from .effects import parse
        ab = ctx.artboard.name
        if not isinstance(var, str):
            self.rep.add(ab, "unsupported", el, "ae: effect on an element without a layer")
            return
        if is_a(el.tag, "Node") and not is_a(el.tag, "Drawable"):
            self.rep.add(ab, "approx", el, "ae: effect on a group → applied to its null (no visual effect): put the comment before a Shape/Image/Text")
        for c in el.ae:
            fxs = parse(c)
            if not fxs:
                continue
            for match, params in fxs:
                if match == "?":
                    self.rep.add(ab, "unsupported", el, f"ae: {params.get('name')}" + ("" if "(" in str(params.get("name")) else f" unknown (names: {', '.join(sorted(self.effect_names()))}, or `effect \"<matchName>\" 0001=…`)"))
                    continue
                self.W(f'try {{ addFx({var}, {js(match)}, {js(params)}); }} catch (eF) {{ log({js("effect FAILED " + match + " on " + el.name)} + ": " + eF.toString()); }}')
                self.rep.add(ab, "converted", el, f"{c[:60]} → AE {match}")

    @staticmethod
    def effect_names():
        from .effects import NAMED
        return NAMED.keys()

    @staticmethod
    def path_of(el):
        """stable address of an id-less element: names from the nearest ancestor that has an id, e.g. '0:101/Layout/Cell2'
        (a duplicate name among siblings gets '#n'); inserting or removing other siblings does not change it"""
        parts = []
        while el is not None and el.parent is not None and not el.id:
            same = [c for c in el.parent.children if c.name == el.name]
            parts.append(el.name + (f"#{same.index(el)}" if len(same) > 1 else ""))
            el = el.parent
        return re.sub(r"\s+", "_", (el.id if el is not None and el.id else "") + "/" + "/".join(reversed(parts)))

    @staticmethod
    def is_element(c):
        return is_a(c.tag, "Component") and not is_a(c.tag, "Animation") and not c.tag.startswith("ScriptInput") and c.tag not in (
            "StateMachine", "LayoutComponentStyle", "Fill", "Stroke", "ClippingShape", "DataBindContext", "Constraint") \
            and not is_a(c.tag, "Constraint") and c.tag not in ("Skin", "Tendon", "Weight", "CubicWeight")

    def replay_subtree(self, el, parent, ctx, why):
        """Placeholder null + CLI replay of the element (exact pixels) for what AE cannot express."""
        J = self.W
        var = J.var("nul")
        J(f'var {var} = {ctx.comp}.layers.addNull(); {var}.name = {js("[replay] " + el.name)}; {var}.label = 4;')
        if parent:
            J(f"{var}.parent = {parent};")
        J(f'tr({var}, "ADBE Anchor Point").setValue([0, 0]); tr({var}, "ADBE Opacity").setValue(100);')
        self.base_transform(var, el, ctx)
        if self.replay:
            self.emit_replay(el, var, ctx, why)
        else:
            self.rep.add(ctx.artboard.name, "unsupported", el, f"{why}: placeholder only (run without --no-replay to render it)")
        J(f"{var}.moveToBeginning();")
        return var

    def needs_replay(self, el):
        """Constraints (IK...) and skinned meshes are solved by the Rive runtime only -> replay the subtree."""
        cons = RIG.constraints_in(el)
        if cons:
            return "constraint " + ", ".join(sorted({c.tag for c in cons}))
        if is_a(el.tag, "Image") and el.find("Mesh") is not None and RIG.has_skin(el):
            return "skinned mesh"
        return None

    def emit_element(self, el, parent, ctx):
        tag = el.tag
        ab = ctx.artboard.name
        why = self.needs_replay(el)
        if why and tag not in ("ScriptedDrawable", "ScriptedLayout"):
            # replay the smallest subtree that contains the whole solve: for bones, the rig root
            if is_a(tag, "Bone"):
                top = el
                while top.parent is not None and is_a(top.parent.tag, "Bone"):
                    top = top.parent
                if top is not el:
                    return None            # handled when the rig root is reached
            return self.replay_subtree(el, parent, ctx, why)
        if is_a(tag, "Bone"):
            return self.emit_bone(el, parent, ctx)
        if tag == "NestedArtboard":
            return self.emit_nested(el, parent, ctx)
        if is_a(tag, "Image"):
            return self.emit_image(el, parent, ctx)
        if is_a(tag, "Text"):
            return self.emit_text(el, parent, ctx)
        if is_a(tag, "Shape"):
            return self.emit_shape(el, parent, ctx)
        if tag in ("ScriptedDrawable", "ScriptedLayout"):
            script = self.p.assets.get(el.get("scriptAssetId"))
            sname = script.get("file") if script is not None else el.get("scriptAssetId")
            inputs = [c for c in el.children if c.tag == "ScriptInputArtboard"]
            fxm = self.fxlib_of(script)
            if fxm is not None and any(c.name in ("fxSource", "source") for c in inputs):
                return self.emit_fxlib(el, fxm, parent, ctx)
            nul = self.emit_node(el, parent, ctx, placeholder=True)
            for inp in inputs:
                sub = self.p.by_id.get(inp.get("artboardId"))
                if sub is None:
                    continue
                comp = self.artboard_comp(sub)
                comp = self.script_fx(el, script, sub, comp, ctx)
                lv = self.W.var("sa")
                self.W(f'var {lv} = {ctx.comp}.layers.add({comp}); {lv}.name = {js(sub.name + " (input " + inp.name + ")")}; {lv}.collapseTransformations = true;')
                self.W(f'{lv}.parent = {nul}; resetTf({lv}, 0, 0, 0, 0);')
            if not inputs:
                if self.replay:
                    self.emit_replay(el, nul, ctx, sname)
                else:
                    self.rep.add(ab, "unsupported", el, f"{tag} (Luau {sname}): placeholder null only (run without --no-replay to render it)")
            return nul
        if is_a(tag, "LayoutComponent"):
            style = self.p.by_id.get(el.get("styleId"))
            flex = style is not None and any(k for k in style.attrs if k not in ("name", "id"))
            if flex and self.replay:
                return self.replay_subtree(el, parent, ctx, "flex layout (resolved by the Rive runtime only)")
            self.rep.add(ab, "approx" if flex else "converted", el, "flex layout: static node, children at their declared x/y" if flex else "layout without flex style → static node")
            return self.emit_node(el, parent, ctx)
        if self.layout == "industry" and tag == "Node" and self._node_groupable(el, root=True):
            return self.emit_merged(el, parent, ctx)
        if is_a(tag, "Node") or is_a(tag, "ContainerComponent"):
            return self.emit_node(el, parent, ctx)
        self.rep.add(ab, "unsupported", el, f"{tag}: skipped")
        return None

    def base_transform(self, var, el, ctx, anchor=None, name_prefix=""):
        """Static transform of a TransformComponent + its keys; call AFTER parenting (AE keeps world transforms)."""
        J = self.W
        if anchor is not None:
            J(f'tr({var}, "ADBE Anchor Point").setValue({js(anchor)});')
        leaf = el
        node = self._fold.get(el.id) if el.id else None
        op_mul = 1.0
        if node is not None:
            # folded: the node's transform on the leaf's layer, the leaf's offset in the anchor point
            J(f'var __a = tr({var}, "ADBE Anchor Point").value; tr({var}, "ADBE Anchor Point").setValue([__a[0] - {js(el.num("x"))}, __a[1] - {js(el.num("y"))}]);')
            op_mul = el.num("opacity", 1)
            if node.id and abs(op_mul - 1) > 1e-9:
                self.fold_opacity[node.id] = op_mul
            el = node
        # nodes dissolved above this layer (audit F14): their translation and their static opacity / bindings
        odx, ody, hosts = self._absorbed.get(id(el), (0.0, 0.0, []))
        for hst in hosts:
            if not any(p in ("opacity", "isVisible") for p, _d in self.binds_quiet(hst)):
                op_mul *= hst.num("opacity", 1)
        x, y = el.num("x") + odx, el.num("y") + ody
        if (odx or ody) and el.id:
            self.offsets[el.id] = [odx, ody]
        rot = el.num("rotation") * 180 / math.pi
        sx, sy = el.num("scaleX", 1), el.num("scaleY", 1)
        op = el.num("opacity", 1) * op_mul
        # always written: parenting made AE rewrite the child's transform to keep its world transform (measured)
        J(f'tr({var}, "ADBE Position").setValue([{js(x)}, {js(y)}]); tr({var}, "ADBE Rotate Z").setValue({js(rot)}); tr({var}, "ADBE Scale").setValue([{js(sx * 100)}, {js(sy * 100)}]);')
        if op != 1:
            J(f'tr({var}, "ADBE Opacity").setValue({js(op * 100)});')
        b = blend_name(leaf.get("blendModeValue"))
        if b and b != "srcOver":
            J(f'{var}.blendingMode = BLEND[{js(b)}];')
        J(f'{var}.comment = {js(self.tag_of(el))};')      # the Rive object id: how a rebuild finds this layer again
        if el.id:
            self.layer_of[el.id] = var
        if leaf is not el and leaf.id:
            self.layer_of[leaf.id] = var
        if int(el.num("drawableFlags", 0)) & 1 or int(leaf.num("drawableFlags", 0)) & 1:
            J(f'{var}.enabled = false;')                       # Drawable hidden flag
        J(f"sep({var});")
        kx, ky, kr = ctx.keys(el.id, "x"), ctx.keys(el.id, "y"), ctx.keys(el.id, "rotation")
        ksx, ksy, ko = ctx.keys(el.id, "scaleX"), ctx.keys(el.id, "scaleY"), ctx.keys(el.id, "opacity")
        if kx:
            J(f'keys(tr({var}, "ADBE Position_0"), {ctx.rows(kx, (lambda v: v + odx) if odx else None)});')
        if ky:
            J(f'keys(tr({var}, "ADBE Position_1"), {ctx.rows(ky, (lambda v: v + ody) if ody else None)});')
        if kr:
            J(f'keys(tr({var}, "ADBE Rotate Z"), {ctx.rows(kr, lambda v: v * 180 / math.pi)});')
        if ksx or ksy:
            ksx = ksx or [(0, sx, "hold", None)]
            ksy = ksy or [(0, sy, "hold", None)]
            J(f'keys(tr({var}, "ADBE Scale"), {ctx.rows2(ksx, ksy, lambda a, b: [a * 100, b * 100])});')
        if ko:
            J(f'keys(tr({var}, "ADBE Opacity"), {ctx.rows(ko, lambda v: v * 100 * op_mul)});')
        self.emit_binds(el, {
            "x": (f'tr({var}, "ADBE Position_0")', "%s" + (f" + {js(odx)}" if odx else "")),
            "y": (f'tr({var}, "ADBE Position_1")', "%s" + (f" + {js(ody)}" if ody else "")),
            "rotation": (f'tr({var}, "ADBE Rotate Z")', "%s * 180 / Math.PI"),
            "scaleX": (f'tr({var}, "ADBE Scale")', "[%s * 100, value[1]]"), "scaleY": (f'tr({var}, "ADBE Scale")', "[value[0], %s * 100]"),
            "opacity": (f'tr({var}, "ADBE Opacity")', "%s * " + js(100 * op_mul))}, ctx,
            extra=[(h, {"opacity": (f'tr({var}, "ADBE Opacity")', "value * %s"), "isVisible": (f'tr({var}, "ADBE Opacity")', "%s")}) for h in hosts])

    # ---- ViewModels (phase 2): one control comp per ViewModel, bindings become expressions
    def build_viewmodels(self):
        J = self.W
        kinds = {"ViewModelPropertyNumber": "number", "ViewModelPropertyBoolean": "boolean", "ViewModelPropertyColor": "color",
                 "ViewModelPropertyString": "string", "ViewModelPropertyEnum": "enum", "ViewModelPropertyTrigger": "trigger",
                 "ViewModelPropertyList": "list", "ViewModelPropertyViewModel": "viewmodel"}
        for vm in self.p.viewmodels:
            props = [c for c in vm.children if c.tag in kinds]
            if not props:
                continue
            inst = None
            for c in vm.children:
                if c.tag == "ViewModelInstance" and (c.id == vm.get("defaultInstanceId") or inst is None):
                    inst = c
            values = {}
            if inst is not None:
                for v in inst.children:
                    values[v.get("viewModelPropertyId")] = v.get("propertyValue")
            cname = f"VM \u00b7 {vm.name}"
            self.vm_comps[vm.id] = cname
            cv = J.var("vmc")
            self.mark(f"vm:{vm.id}", cv, cname)
            J(f'var {cv} = app.project.items.addComp({js(cname)}, 400, 400, 1, 1, FPS); {cv}.parentFolder = ROOTF; {cv}.comment = {js("vm:" + vm.id)};')
            J(f'var VML = {cv}.layers.addNull(); VML.name = "ViewModel"; VML.label = 9;')
            for pr in props:
                kind = kinds[pr.tag]
                name = pr.name
                raw = values.get(pr.id)
                comp_ref = f'comp({js(cname)})'
                if kind == "number":
                    val = float(raw) if raw not in (None, "") else float(pr.get("propertyValue", 0) or 0)
                    J(f'slider(VML, {js(name)}, {js(val)});')
                    expr = f'{comp_ref}.layer("ViewModel").effect({js(name)})("ADBE Slider Control-0001")'
                elif kind == "boolean":
                    val = (raw == "true")
                    J(f'var cb = VML.property("ADBE Effect Parade").addProperty("ADBE Checkbox Control"); cb.name = {js(name)}; cb.property("ADBE Checkbox Control-0001").setValue({1 if val else 0});')
                    expr = f'{comp_ref}.layer("ViewModel").effect({js(name)})("ADBE Checkbox Control-0001")'
                elif kind == "color":
                    r, g, b, a = argb(raw) if raw else (1, 1, 1, 1)
                    val = (r, g, b, a)
                    J(f'var cc = VML.property("ADBE Effect Parade").addProperty("ADBE Color Control"); cc.name = {js(name)}; cc.property("ADBE Color Control-0001").setValue({js([r, g, b])});')
                    expr = f'{comp_ref}.layer("ViewModel").effect({js(name)})("ADBE Color Control-0001")'
                elif kind == "string":
                    val = raw or ""
                    J(f'var tl = {cv}.layers.addText({js(val)}); tl.name = {js(name)}; tl.guideLayer = true;')
                    expr = f'{comp_ref}.layer({js(name)}).text.sourceText'
                else:
                    val = raw
                    expr = None
                    self.rep.add("view models", "unsupported", pr, f"{kind} property '{name}' of '{vm.name}': no AE control")
                self.vm_props[pr.id] = dict(vm=vm, name=name, kind=kind, value=val, expr=expr)
            self.unmark()
            self.rep.add("view models", "converted", vm, f"{len(props)} properties → comp '{cname}' (sliders / checkboxes / colours / texts)")

    def vm_value(self, name, default=None):
        for d in self.vm_props.values():
            if d["name"] == name and d["kind"] == "number":
                return d["value"]
        return default

    def binds(self, el):
        """DataBindContext children of an element -> [(property name, vm prop dict)]; unsupported ones reported."""
        out = []
        for b in el.children:
            if b.tag != "DataBindContext":
                continue
            path = (b.get("sourcePathIds") or "").split("-")
            pk = b.get("propertyKey")
            if not pk or len(path) < 2:
                continue
            pid = path[-1]
            d = self.vm_props.get(pid)
            if d is None or d["expr"] is None:
                self.rep.add("view models", "unsupported", el, f"binding of {prop_name(pk)} to an unconverted property ({pid})")
                continue
            if len(path) > 2:
                self.rep.add("view models", "approx", el, f"nested view-model path {b.get('sourcePathIds')}: bound to the leaf property only")
            if b.get("converterId"):
                self.rep.add("view models", "approx", el, f"binding of {prop_name(pk)} uses a converter ({b.get('converterId')}): raw value used")
            out.append((prop_name(pk), d))
        return out

    def emit_binds(self, el, targets, ctx, extra=None):
        """targets = {rive property name: (js property accessor, template with %s = the control expression)};
        extra = [(element, targets)] whose bindings chain after el's on the same AE properties"""
        ab = ctx.artboard.name
        by_acc = {}
        for el, targets in [(el, targets)] + list(extra or []):
          for prop, d in self.binds(el):
            if prop not in targets:
                self.rep.add(ab, "unsupported", el, f"binding of '{prop}' ({d['kind']}): no AE target")
                continue
            acc, tpl = targets[prop]
            if "%s" not in tpl:
                tpl = "%s"
            expr = tpl % d["expr"]
            if d["kind"] == "boolean" and prop in ("opacity", "isVisible"):
                # a Property object is always truthy in an expression: compare its value; the element's own opacity stays
                expr = f'value * (({d["expr"]}) == 1 ? 1 : 0)'
            by_acc.setdefault(acc, []).append(expr)
            self.rep.add(ab, "converted", el, f"'{prop}' bound to ViewModel '{d['name']}' → expression")
        for acc, exprs in by_acc.items():
            if len(exprs) == 1:
                self.W(f"{acc}.expression = {js(exprs[0])};")
            else:
                # several bindings on ONE AE property (scaleX + scaleY -> Scale): chained, each reads the previous result
                # as `value` (assigning them one after the other kept only the last; audit F22)
                chain = "var v = value; " + " ".join(f"v = {re.sub(r'(?<![A-Za-z0-9_.])value(?![A-Za-z0-9_])', 'v', e)};" for e in exprs) + " v"
                self.W(f"{acc}.expression = {js(chain)};")

    def emit_replay(self, el, nul, ctx, sname):
        """Scripted element -> PNG sequence rendered by the CLI (element + ancestors only), placed in world space."""
        J = self.W
        ab = ctx.artboard
        n = max(1, int(round(self.duration_of(ab, ctx.anim) * self.fps)))
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{ab.name}_{el.name}_{el.id}" + (f"_{ctx.anim.name}" if ctx.anim else ""))
        out_dir = os.path.join(self.out_dir, "replay", safe)
        try:
            paths = RP.render_sequence(self.p.dir, ab.name, el.id, out_dir, n, self.fps, log=self.log_fn, animation_id=ctx.anim.id if ctx.anim and not getattr(ctx.anim, "chain", False) else None)
        except Exception as ex:
            self.rep.add(ab.name, "unsupported", el, f"replay of {sname} failed: {ex}")
            return None
        if not paths or not os.path.exists(paths[0]):
            self.rep.add(ab.name, "unsupported", el, f"replay of {sname}: no frame rendered")
            return None
        var = J.var("seqL")
        J(f'var {var}F = (function () {{ var io = new ImportOptions(new File({js(paths[0])})); io.sequence = true; var it = app.project.importFile(io); try {{ it.mainSource.conformFrameRate = FPS; }} catch (ec) {{}} it.parentFolder = FOOT; return it; }})();')
        J(f'var {var} = {ctx.comp}.layers.add({var}F); {var}.name = {js("[replay] " + el.name)}; resetTf({var}, 0, 0, 0, 0);')
        # world space: above everything the element was above in Rive is not knowable after flattening -> keep z-order
        J(f'{var}.moveBefore({nul});')
        # the frames were rendered with the ViewModel's DEFAULT values: an opacity bound on the element or an ancestor
        # (a render pass hiding photos, a visibility toggle) must still act on the sequence, which has no parent
        # (film test: the replayed photo stayed in the text-only pass and darkened the card through the shader)
        factors = []
        cur = el
        while cur is not None and cur.tag != "Artboard":
            for prop, d in self.binds_quiet(cur):
                if prop not in ("opacity", "isVisible"):
                    continue
                for p2, dd in self.binds(cur):
                    if p2 != prop or dd.get("expr") is None:
                        continue
                    v0 = dd.get("value")
                    if dd["kind"] == "boolean":
                        factors.append(f"(({dd['expr']}) == 1 ? 1 : 0)" + ("" if v0 else " * 0"))
                    elif isinstance(v0, (int, float)) and abs(v0) > 1e-9:
                        factors.append(f"({dd['expr']}) / {js(float(v0))}")
            cur = cur.parent
        if factors:
            J(f'tr({var}, "ADBE Opacity").expression = {js("value * " + " * ".join(dict.fromkeys(factors)))};')
        self.rep.add(ab.name, "approx", el, f"{sname} replayed by the CLI as a {n}-frame PNG sequence (element + ancestors, two-pass alpha)")
        return var

    FX_NAMES = {"grain": ("grain", "noise"), "wobble": ("wobble", "wiggle", "shake"), "displace": ("displace", "edge", "photoEdge", "textEdge", "turbulence"),
                "vignette": ("vignette",), "step": ("tick", "step", "stepFps", "posterize")}

    # ---- AE-compatible WGSL effects (ae2rml/fxlib): the node maps back to the NATIVE After Effects effect
    def fxlib_of(self, script):
        """manifests of an fxlib effect-stack node (fx_<slug>[__<slug>…].luau written by ae2rml), else None"""
        f = script.get("file") if script is not None else None
        m = re.match(r"fx_(\w+)\.luau$", os.path.basename(f or ""))
        if not m:
            return None
        from .ae2rml import fxlib as FXL
        slugs, groups = FXL.parse_stack_name("fx_" + m.group(1))
        try:
            mans = [FXL.manifest(slug) for slug in slugs]
        except Exception:
            return None
        if not all(x.get("aeMatchName") for x in mans):
            return None
        return {"mans": mans, "groups": groups or [list(range(1, len(mans) + 1))]}

    def emit_fxlib(self, el, fxm, parent, ctx):
        """fxlib node -> its source artboard as a precomp layer carrying the NATIVE AE effects in the same order:
        parameters in AE units (static or keyed like the Rive inputs e<i>_<field>), fxMix -> Effect Opacity
        (Compositing Options; exact for one effect, per-effect for a stack), fxTime -> Time Remap when it is not
        the comp's own time"""
        J = self.W
        ab = ctx.artboard.name
        mans, groups = fxm["mans"], fxm["groups"]
        group_of = {i: j for j, g in enumerate(groups, 1) for i in g}
        single = len(groups) == 1
        nul = self.emit_node(el, parent, ctx, placeholder=True)
        ins = {c.name: c for c in el.children if c.tag.startswith("ScriptInput")}
        src_in = ins.get("fxSource") or ins.get("source")
        sub = self.p.by_id.get(src_in.get("artboardId")) if src_in is not None else None
        if sub is None:
            return nul
        comp = self.artboard_comp(sub)
        lv = J.var("fxl")
        J(f'var {lv} = {ctx.comp}.layers.add({comp}); {lv}.name = {js(el.name or sub.name)};')
        J(f'{lv}.parent = {nul}; resetTf({lv}, 0, 0, 0, 0);')
        J(f'{lv}.comment = {js(self.tag_of(el) + "+fx")};')
        tin = ins.get("fxTime") or ins.get("time")
        tk = ctx.keys(tin.id, "propertyValue") if tin is not None else None
        afps = (ctx.anim.fps if ctx.anim else self.fps) or self.fps
        if tk and any(abs(v - f / float(afps)) > 1e-3 for f, v, _i, _e in tk):
            J(f'{lv}.timeRemapEnabled = true; keys({lv}.property("ADBE Time Remapping"), {ctx.rows(tk)});')
        blends = []
        for i, m in enumerate(mans, 1):
            j = group_of.get(i, 1)
            am = (ins.get("fxMix") or ins.get("amount")) if single else ins.get(f"g{j}Mix")
            ak = ctx.keys(am.id, "propertyValue") if am is not None else None
            bl = ins.get("fxBlend") if single else ins.get(f"g{j}Blend")
            if bl is not None and bl.num("propertyValue") and j not in blends:
                blends.append(j)
            fxe = J.var("fxe")
            J(f'var {fxe} = {lv}.property("ADBE Effect Parade").addProperty({js(m["aeMatchName"])});')
            for p in m["params"]:
                idx = int(p["ae"])
                f = f"e{i}_{p['field']}"
                if p["kind"] == "color":
                    c = ins.get(f)
                    hexv = (c.get("propertyValue") if c is not None else None) or ""
                    if len(hexv) == 8:
                        a, r, g, b = (int(hexv[k:k + 2], 16) / 255.0 for k in (0, 2, 4, 6))
                        J(f'try {{ {fxe}.property({idx}).setValue({js([r, g, b, a])}); }} catch (e) {{}}')
                    continue
                if p["kind"] == "point":
                    cx, cy = ins.get(f + "X"), ins.get(f + "Y")
                    if cx is None or cy is None:
                        continue
                    vx, vy = cx.num("propertyValue"), cy.num("propertyValue")
                    J(f'try {{ {fxe}.property({idx}).setValue({js([vx, vy])}); }} catch (e) {{}}')
                    kx, ky = ctx.keys(cx.id, "propertyValue"), ctx.keys(cy.id, "propertyValue")
                    if kx or ky:
                        J(f'try {{ keys({fxe}.property({idx}), {ctx.rows2(kx or [(0, vx, "hold", None)], ky or [(0, vy, "hold", None)], lambda a, b: [a, b])}); }} catch (e) {{}}')
                    continue
                c = ins.get(f)
                if c is None:
                    continue
                J(f'try {{ {fxe}.property({idx}).setValue({js(c.num("propertyValue"))}); }} catch (e) {{}}')
                k = ctx.keys(c.id, "propertyValue")
                if k:
                    J(f'try {{ keys({fxe}.property({idx}), {ctx.rows(k)}); }} catch (e) {{}}')
            if am is not None:
                eo = f'{fxe}.property("ADBE Effect Built In Params").property("ADBE Effect Mask Opacity")'
                if ak:
                    J(f'try {{ keys({eo}, {ctx.rows(ak, lambda v: v * 100)}); }} catch (e) {{}}')
                elif abs(am.num("propertyValue", 1) - 1) > 1e-6:
                    J(f'try {{ {eo}.setValue({js(am.num("propertyValue", 1) * 100)}); }} catch (e) {{}}')
        names = ", ".join(m["aeMatchName"] for m in mans)
        multi = any(len(g) > 1 for g in groups)
        self.rep.add(ab, "approx" if (multi or blends) else "converted",
                     el, f"fxlib node → native AE effect{'s' if len(mans) > 1 else ''} {names} on the precomp of '{sub.name}'"
                     + (" (a group's mix → each of its effects' Effect Opacity: exact at 0 and 100 %)" if multi else "")
                     + (f" (blend mode of group{'s' if len(blends) > 1 else ''} {', '.join(map(str, blends))} not rebuilt: "
                        "use an adjustment layer in AE)" if blends else ""))
        return nul

    def script_fx(self, el, script, sub, comp, ctx):
        """Shader of a post-process script recognised by its uniform names -> '<artboard> + FX' comp with AE effects,
        Posterize Time applied on top when the script steps its clock. Returns the comp var to place."""
        J = self.W
        ab = ctx.artboard.name
        sfile = script.get("file") if script is not None else None
        if not sfile:
            return comp
        defaults, shader = RP.luau_defaults(os.path.join(self.p.dir, sfile))
        if not shader:
            self.rep.add(ab, "approx", el, f"Luau {sfile}: no shader call found; its artboard input is placed as a precomp without the script's drawing")
            return comp
        shader_path = os.path.join(self.p.dir, shader + ".wgsl")
        fields = RP.wgsl_params(shader_path)
        found = {}
        for fx, names in self.FX_NAMES.items():
            for f in fields:
                if any(f.lower() == n.lower() for n in names):
                    found[fx] = f
                    break
        if not found and not os.path.exists(shader_path):
            self.rep.add(ab, "unsupported", el, f"shader {shader}.wgsl: not found; artboard input placed without post-process")
            return comp

        def val(*names, default):
            for n in names:
                v = self.vm_value(n)
                if v is not None:
                    return v
                if n in defaults:
                    return defaults[n]
            return default
        w, h = sub.num("width", 1920), sub.num("height", 1080)
        dur = self.chain_duration(sub)
        fxc = J.var("fxc")
        J(f'var {fxc} = app.project.items.addComp({js(sub.name + " + FX")}, {int(round(w))}, {int(round(h))}, 1, {js(dur)}, FPS); {fxc}.parentFolder = SUBS;')
        J(f'var fxl = {fxc}.layers.add({comp}); fxl.name = {js(sub.name)};')
        # 1. the Rive Shader plugin, when installed: the .wgsl runs as is with the ViewModel / Luau values
        passes = RS.passes_for(self.p.dir, el.name)
        plugin_lines, plugin_notes, step_fps = RS.jsx_apply("fxl", shader_path, os.path.join(self.p.dir, sfile), lambda n, d: val(n, default=d), passes=passes)
        J("if (RIVE_SHADER) {")
        if passes:
            # ae_passes.json: one VM copy + one clone of the artboard tree per pass (the Luau instantiates the artboard
            # once per pass with different ViewModel values); the effect's source and its texture layers use the clones
            vm_names = [n for n in self.vm_comps.values()]
            vm_pairs = ", ".join(js(n) for n in vm_names)
            J(f"  var passVms = [{vm_pairs}];")
            J("  function passClone(passName, overrides, srcComp) { var pairs = []; for (var v = 0; v < passVms.length; v++) { var vmc = compByName(passVms[v]); if (!vmc) continue;"
              " var nv = cloneVm(vmc, passVms[v] + ' [' + passName + ']', overrides); pairs.push(['comp(\"' + jsEsc(passVms[v]) + '\")', 'comp(\"' + jsEsc(nv.name) + '\")']); pairs.push(['comp(\"' + passVms[v] + '\")', 'comp(\"' + nv.name + '\")']); }"
              " return cloneTree(srcComp, passName, pairs, {}); }")
            tex_idx = RS.texture_params(shader_path)
            # texture passes first (cloned from the untouched ViewModel), then the source pass's values go straight
            # into the master VM comps: the tree with the proper names IS the source pass (audit F1)
            for tname, over in passes.items():
                if tname == "source" or tname not in tex_idx:
                    continue
                J(f"  var texPass = passClone({js(el.name + ' ' + tname)}, {js(over)}, {comp}); var texL = {fxc}.layers.add(texPass); texL.name = {js(tname)}; texL.enabled = false; texL.moveToEnd();")
                plugin_lines.append(f"rsfx.property({tex_idx[tname]}).setValue(texL.index);")
                plugin_notes.append(f"texture {tname} = clone of the comps that read the ViewModel, with VM {over} (layer param)")
            if "source" in passes:
                J(f"  for (var v = 0; v < passVms.length; v++) {{ var vmc = compByName(passVms[v]); if (vmc) setVm(vmc, {js(passes['source'])}); }}")
                plugin_notes.append(f"source = the artboard itself, its ViewModel set to {passes['source']}")
        for line in plugin_lines:
            J("  " + line)
        J("} else {")
        # 2. otherwise the heuristic translation to native AE effects
        notes = []
        if not found:
            notes.append(f"no recognised uniform ({', '.join(fields)}): without the plugin the artboard input is placed untouched")
        if "wobble" in found:
            px = val("wiggle", "wobble", default=1.0)
            hz = val("wiggleFps", "fxFps", default=6.0)
            J(f'  tr(fxl, "ADBE Position").expression = {js(f"posterizeTime({hz}); wiggle({hz}, {px})")};')
            notes.append(f"wobble {px}px @ {hz}Hz → wiggle()")
        if "displace" in found:
            px = val("photoEdge", "displace", "edge", default=1.0)
            J(f'  var td = fxl.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace"); td.name = "Edge displace"; td.property("ADBE Turbulent Displace-0002").setValue({js(px * 2)}); td.property("ADBE Turbulent Displace-0003").setValue(6); td.property("ADBE Turbulent Displace-0005").setValue(2); td.property("ADBE Turbulent Displace-0006").expression = "time*360";')
            notes.append(f"edge displace {px}px → Turbulent Displace")
        if "grain" in found:
            g = val("grain", default=0.05)
            J(f'  var nz = fxl.property("ADBE Effect Parade").addProperty("ADBE Noise2"); nz.name = "Grain"; nz.property("ADBE Noise2-0001").setValue({js(g * 100)}); nz.property("ADBE Noise2-0002").setValue(0);')
            notes.append(f"grain {g} → Noise {g * 100:.0f}%")
        if "vignette" in found:
            v = val("vignette", default=0.1)
            c = 0.5523
            rx, ry, cx, cy = w * 0.62, h * 0.72, w / 2, h / 2
            ev = [[cx, cy - ry], [cx + rx, cy], [cx, cy + ry], [cx - rx, cy]]
            ei = [[-rx * c, 0], [0, -ry * c], [rx * c, 0], [0, ry * c]]
            eo = [[rx * c, 0], [0, ry * c], [-rx * c, 0], [0, -ry * c]]
            J(f'  var vg = {fxc}.layers.addSolid([0, 0, 0], "Vignette", {int(w)}, {int(h)}, 1); vg.name = "Vignette"; tr(vg, "ADBE Opacity").setValue({js(v * 100)});')
            J(f'  var vm = vg.property("ADBE Mask Parade").addProperty("ADBE Mask Atom"); vm.property("ADBE Mask Shape").setValue(shapeFrom({js(ev)}, {js(ei)}, {js(eo)}, true)); vm.inverted = true; vm.property("ADBE Mask Feather").setValue([500, 500]);')
            notes.append(f"vignette {v} → masked solid")
        J("}")
        out = fxc
        # the stop-motion clock also steps the source animation (Rive advances the artboard by 1/stepFps): Posterize Time
        fps_step = step_fps or (val("stepFps", "step", default=None) if "step" in found else None)
        if fps_step:
            pc = J.var("pc")
            J(f'var {pc} = app.project.items.addComp({js(sub.name + " + FX (stepped)")}, {int(round(w))}, {int(round(h))}, 1, {js(dur)}, FPS); {pc}.parentFolder = SUBS;')
            J(f'var pl = {pc}.layers.add({fxc}); pl.name = {js(sub.name + " + FX")}; var pt = pl.property("ADBE Effect Parade").addProperty("ADBE Posterize Time"); pt.name = "Stop motion"; pt.property("ADBE Posterize Time-0001").setValue({js(fps_step)});')
            notes.append(f"stepped clock → Posterize Time {fps_step}")
            plugin_notes.append(f"stepped clock → Posterize Time {fps_step}")
            out = pc
        self.rep.add(ab, "approx", el, f"shader {shader}.wgsl: with the Rive Shader plugin (RIVE RiveShader) → " + "; ".join(plugin_notes)
                     + " | without it → " + "; ".join(notes) + " (values from the ViewModel / Luau defaults)")
        return out

    # ---- Node
    def emit_node(self, el, parent, ctx, placeholder=False):
        J = self.W
        clip = el.find("ClippingShape") if not placeholder else None
        if clip is not None:
            src = self.p.by_id.get(clip.get("sourceId"))
            if src is not None and is_a(src.tag, "Shape") and src.parent is el and not RIG.has_skin(src):
                # the clip source is one of the node's own children: the precomp carries a Stencil Alpha copy of it,
                # and the parent comp sees one precomp layer (no outer null nor matte; audit F6)
                return self.emit_clipped(el, None, parent, ctx, clip, stencil=True)
        if clip is not None and self.layout == "industry" and el.id not in self._rig_targets(ctx.artboard):
            src = self.p.by_id.get(clip.get("sourceId"))
            if src is not None and not any(e is src for e in el.iter()):
                # the clip source lives elsewhere: the null would only hold the node's transform, which the precomp's
                # root null already carries — no outer null (it was left with no child)
                return self.emit_clipped(el, None, parent, ctx, clip)
        var = J.var("nul")
        label = ("[script] " if placeholder else "") + el.name
        J(f'var {var} = {ctx.comp}.layers.addNull(); {var}.name = {js(label)}; {var}.label = {4 if placeholder else 3};')
        if parent:
            J(f"{var}.parent = {parent};")
        J(f'tr({var}, "ADBE Anchor Point").setValue([0, 0]); tr({var}, "ADBE Opacity").setValue(100);')
        self.base_transform(var, el, ctx)
        clip = el.find("ClippingShape")
        if clip is not None:
            return self.emit_clipped(el, var, parent, ctx, clip)
        # Rive: a group's opacity is multiplied into every descendant (rive docs transforms) = AE per-layer opacity
        # with the propagateOpacity expression; no group compositing.
        if el.tag == "Solo":
            # the slider first: each child block sets its own "shown by index" expression (render() rebuilds children alone)
            ks = ctx.keys(el.id, "activeComponentId")
            kids_ids = [c.id for c in el.children if c.id and self.is_element(c)]
            self.solo_kids[el.id] = kids_ids
            rows = [[0, kids_ids.index(el.get("activeComponentId")) if el.get("activeComponentId") in kids_ids else -1, "h", 0, 0, 0, 0]]
            if ks:
                rows = ([] if ks[0][0] == 0 else rows) + [[f * ctx.fscale, kids_ids.index(v) if v in kids_ids else -1, "h", 0, 0, 0, 0] for f, v, _, _ in ks]
            J(f'var solo = slider({var}, "Solo active", 0); keys(solo, {js(rows)});')
            self.rep.add(ctx.artboard.name, "converted", el, f"Solo → 'Solo active' slider on the null, children shown by index ({'keyed' if ks else 'static'})")
        # a null's opacity does not reach its children in AE: each child block reads it through an expression (propUnder)
        self.emit_children(el, var, ctx)
        J(f"{var}.moveToBeginning();")
        return var

    def emit_clipped(self, el, nul, parent, ctx, clip, stencil=False):
        """Node with a ClippingShape: its subtree goes into a precomp built in the PARENT's space (a root null inside
        carries the node's transform + keys), matted by a copy of the clip source. A collapsed precomp with a track
        matte is rasterised at its comp size (measured), so the precomp is not collapsed: its canvas is the artboard
        for a top-level node, 3x the artboard (centred) for a deeper one."""
        J = self.W
        ab = ctx.artboard
        src = self.p.by_id.get(clip.get("sourceId")) if clip is not None else None
        sub = J.var("sub")
        w, h = ab.num("width", 1920), ab.num("height", 1080)
        big = parent is not None
        cw, ch = (3 * w, 3 * h) if big else (w, h)
        offx, offy = (w, h) if big else (0.0, 0.0)
        dur = self.duration_of(ab, ctx.anim)
        sname = el.name + (" (clipped)" if clip is not None else " (group)")
        stag = "rive:" + (el.id or sub) + "|" + (ctx.anim.id if ctx.anim else "") + "|clip"
        self.mark(stag, sub, sname)
        J(f"//<head {sub}>")
        J("try {")
        J(f'var {sub} = app.project.items.addComp({js(sname)}, {int(round(cw))}, {int(round(ch))}, 1, {js(max(dur, 1 / self.fps))}, FPS); {sub}.parentFolder = SUBS; {sub}.comment = {js(stag)};')
        subctx = Ctx(self, sub, ab, ctx.anim, ctx.carry)      # what the previous state left applies inside too
        root = J.var("root")
        J(f'var {root} = {sub}.layers.addNull(); {root}.name = {js(el.name)}; {root}.label = 3;')
        J(f'tr({root}, "ADBE Anchor Point").setValue([{js(-offx)}, {js(-offy)}]); tr({root}, "ADBE Opacity").setValue(100);')
        self.base_transform(root, el, subctx)
        J(f'resetProp(tr({root}, "ADBE Opacity"), 100); {root}.blendingMode = BlendingMode.NORMAL;')
        J(f'tagNew({sub}, 0, "head", null);')
        J(f'}} catch (eC) {{ log({js("comp FAILED: " + sname)} + ": " + eC.toString() + " line " + eC.line); }}')
        J("//</head>")
        self.emit_children(el, root, subctx)
        if stencil:
            stag = "rive:" + (el.id or "") + "+clip"
            J(f"//<el {stag} {sub} ->")
            mv = self.emit_shape(src, root, subctx, as_matte=True)
            J(f"stencilize({mv});")
            J(f"//</el {stag} {mv}>")
        self.unmark()
        lv = J.var("clp")
        J(f'var {lv} = {ctx.comp}.layers.add({sub}); {lv}.name = {js(el.name)};')
        if parent:
            J(f"{lv}.parent = {parent};")
        J(f'resetTf({lv}, {js(offx)}, {js(offy)}, 0, 0);')
        op = el.num("opacity", 1)
        if op != 1:
            J(f'tr({lv}, "ADBE Opacity").setValue({js(op * 100)});')
        ko = ctx.keys(el.id, "opacity")
        if ko:
            J(f'keys(tr({lv}, "ADBE Opacity"), {ctx.rows(ko, lambda v: v * 100)});')
        self.emit_binds(el, {"opacity": (f'tr({lv}, "ADBE Opacity")', "%s * 100")}, ctx)
        bm = blend_name(el.get("blendModeValue"))
        if bm and bm != "srcOver":
            J(f'{lv}.blendingMode = BLEND[{js(bm)}];')
        if clip is None:
            if nul:
                J(f"{nul}.moveToBeginning();")
            return lv
        if stencil:
            self.rep.add(ab.name, "converted", el, f"clipped by '{src.name}' → precomp holding a Stencil Alpha copy of the "
                                                   f"clip (one layer here, no outer null nor matte)")
            J(f"{lv}.moveToBeginning();")
            return lv
        node_src = src is not None and src.tag == "Node" and self._matte_groupable(src)
        if src is not None and (is_a(src.tag, "Shape") or node_src):
            # the matte follows the node when the clip source lives inside it, else it stays in the node's parent space;
            # a source elsewhere in the tree (ae2rml's track mattes: « X · matte source » next to the clipped node's
            # parent) gets its own space through an offset null
            inside = any(e is src for e in el.iter())
            host = nul if inside else parent
            host = self.matte_space(src, el if inside else el.parent, host, ctx, ab)
            mv = self.emit_merged_matte(src, host, ctx) if node_src else self.emit_shape(src, host, ctx, as_matte=True)
            J(f'{mv}.comment = {js("rive:" + (el.id or "") + "+matte")};')      # belongs to this element, not to the source
            J(f"{lv}.setTrackMatte({mv}, TrackMatteType.ALPHA);")
            self.rep.add(ab.name, "converted", el, f"clipped by {'the shapes of node ' if node_src else ''}'{src.name}' → precomp + alpha matte")
        else:
            self.rep.add(ab.name, "unsupported", el, "ClippingShape source not found / not a shape")
        if nul:
            J(f"{nul}.moveToBeginning();")
        return lv

    def emit_audio(self, ab, ctx):
        """AudioEvent fired by keyframe callbacks -> an audio layer starting at the callback time ; ae_audio.json side
        file -> extra audio layers ([{"comp": "Main", "file": "build/soundtrack.wav", "at": 0}])."""
        J = self.W
        first = None
        for ev in ab.iter():
            if ev.tag != "AudioEvent":
                continue
            ft = self.footage.get(ev.get("assetId"))
            kt = ctx.keys(ev.id, "trigger")
            if not ft or not kt:
                continue
            for f, _, _, _ in kt:
                av = J.var("au")
                first = first or av
                J(f'var {av} = {ctx.comp}.layers.add({ft}); {av}.name = {js("[audio] " + ev.name)}; {av}.startTime = T({js(f * ctx.fscale)});')
            self.rep.add(ab.name, "converted", ev, f"audio event fired {len(kt)}x → audio layers")
        side = os.path.join(self.p.dir, "ae_audio.json")
        if os.path.exists(side) and ab is self.main_artboard() and not getattr(self, "_audio_done", False):
            self._audio_done = True
            import json as _json
            for item in _json.load(open(side, encoding="utf-8")):
                if item.get("comp", ab.name) != ab.name:
                    continue
                path = os.path.join(self.p.dir, item["file"])
                av = J.var("au")
                first = first or av
                J(f'var {av}F = importFile({js(path)}); {av}F.parentFolder = FOOT; var {av} = {ctx.comp}.layers.add({av}F); {av}.name = {js("[audio] " + os.path.basename(path))}; {av}.startTime = {js(float(item.get("at", 0)))};')
                self.rep.add(ab.name, "converted", None, f"ae_audio.json: {item['file']} at {item.get('at', 0)}s")
        return first

    # ---- Bones: a null per bone (FK is parenting); a Bone sits at its parent's tip
    def emit_bone(self, el, parent, ctx):
        J = self.W
        var = J.var("bone")
        J(f'var {var} = {ctx.comp}.layers.addNull(); {var}.name = {js("[bone] " + el.name)}; {var}.label = 10;')
        if parent:
            J(f"{var}.parent = {parent};")
        J(f'tr({var}, "ADBE Anchor Point").setValue([0, 0]); tr({var}, "ADBE Opacity").setValue(100);')
        rot = el.num("rotation") * 180 / math.pi
        sx, sy = el.num("scaleX", 1), el.num("scaleY", 1)
        if el.tag == "RootBone":
            x, y = el.num("x"), el.num("y")
        else:
            par = el.parent
            x, y = (par.num("length") if par is not None and is_a(par.tag, "Bone") else 0.0), 0.0
        J(f'tr({var}, "ADBE Position").setValue([{js(x)}, {js(y)}]); tr({var}, "ADBE Rotate Z").setValue({js(rot)}); tr({var}, "ADBE Scale").setValue([{js(sx * 100)}, {js(sy * 100)}]);')
        J(f'{var}.comment = {js(self.tag_of(el))};')
        J(f"sep({var});")
        kr = ctx.keys(el.id, "rotation")
        if kr:
            J(f'keys(tr({var}, "ADBE Rotate Z"), {ctx.rows(kr, lambda v: v * 180 / math.pi)});')
        if el.tag == "RootBone":
            for prop, ae in (("x", "ADBE Position_0"), ("y", "ADBE Position_1")):
                k = ctx.keys(el.id, prop)
                if k:
                    J(f'keys(tr({var}, "{ae}"), {ctx.rows(k)});')
        else:
            par = el.parent
            kl = ctx.keys(par.id, "length") if (par is not None and is_a(par.tag, "Bone") and par.id) else None
            if kl:
                J(f'keys(tr({var}, "ADBE Position_0"), {ctx.rows(kl)});')
        for prop, fn in (("scaleX", None), ("scaleY", None)):
            pass
        ksx, ksy = ctx.keys(el.id, "scaleX"), ctx.keys(el.id, "scaleY")
        if ksx or ksy:
            J(f'keys(tr({var}, "ADBE Scale"), {ctx.rows2(ksx or [(0, sx, "hold", None)], ksy or [(0, sy, "hold", None)], lambda a, b: [a * 100, b * 100])});')
        self.emit_children(el, var, ctx)
        J(f"{var}.moveToBeginning();")
        self.rep.add(ctx.artboard.name, "converted", el, "bone → null (FK by parenting)")
        return var

    # ---- Nested artboard
    def emit_nested(self, el, parent, ctx):
        J = self.W
        ab = self.p.by_id.get(el.get("artboardId"))
        if ab is None:
            self.rep.add(ctx.artboard.name, "unsupported", el, "nested artboard not found")
            return None
        inputs = [c for c in el.children if is_a(c.tag, "NestedInput")]
        if any(ctx.keys(c.id, p) for c in inputs for p in ("nestedValue", "fire")):
            return self.replay_subtree(el, parent, ctx, "nested state-machine inputs keyed by the parent")
        remap = el.find("NestedRemapAnimation")
        simple = el.find("NestedSimpleAnimation")
        if remap is not None:
            anim = self.p.animations.get(remap.get("animationId"))
            comp = self.anim_comp(ab, anim)
        elif simple is not None:
            anim = self.p.animations.get(simple.get("animationId"))
            comp = self.anim_comp(ab, anim)
        else:
            comp = self.artboard_comp(ab)
            anim = None
        var = J.var("nst")
        J(f'var {var} = {ctx.comp}.layers.add({comp}); {var}.name = {js(el.name)};')
        if ab.get("clip") != "true":
            J(f"{var}.collapseTransformations = true;")
        if parent:
            J(f"{var}.parent = {parent};")
        J(f'tr({var}, "ADBE Anchor Point").setValue([0, 0]);')
        self.base_transform(var, el, ctx)
        if remap is not None and anim is not None:
            kt = ctx.keys(remap.id, "time")
            dur_s = anim.duration / anim.fps
            J(f'{var}.timeRemapEnabled = true; var trp = {var}.property("ADBE Time Remapping");')
            if kt:
                # NestedRemapAnimation.time is a fraction 0..1 of the animation (measured), keyframes in comp time.
                # time 1.0 = Rive's end frame; AE shows NOTHING at exactly the source duration (film-test 7.24 s: the
                # board scrubbed to its end vanished) → capped at the last real frame
                last = max(0.0, dur_s - 1.0 / (anim.fps or self.fps))
                J(f'remapKeys(trp, {ctx.rows(kt, lambda v: min(v * dur_s, last))});')
                J(f'{var}.outPoint = {ctx.comp}.duration;')
            else:
                t0 = ctx.carry.get((remap.id, "time"), remap.num("time"))      # unkeyed here: the value the previous state left
                t0 = min(t0 * dur_s, max(0.0, dur_s - 1.0 / (anim.fps or self.fps)))
                J(f"remapSet(trp, [[0, {js(t0)}], [{ctx.comp}.duration, {js(t0)}]]);")
                # held on one frame for the whole parent: the layer must last as long (the trailer's Letterbox, a
                # 26-frame comp with an unkeyed remap, vanished after 0.43 s)
                J(f'{var}.outPoint = {ctx.comp}.duration;')
            self.rep.add(ctx.artboard.name, "converted", el, f"scrubs '{ab.name} · {anim.name}' → time remap")
        elif simple is not None:
            sp = simple.num("speed", 1)
            if sp != 1:
                J(f'{var}.timeRemapEnabled = true; var trp = {var}.property("ADBE Time Remapping"); remapSet(trp, [[0, 0], [{ctx.comp}.duration, {ctx.comp}.duration * {js(sp)}]]);')
                self.rep.add(ctx.artboard.name, "converted", el, f"NestedSimpleAnimation speed {sp} → time remap slope")
        if anim is not None and anim.quantize:
            J(f'var pq = {var}.property("ADBE Effect Parade").addProperty("ADBE Posterize Time"); pq.name = "Rive quantize"; pq.property("ADBE Posterize Time-0001").setValue({js(anim.fps)});')
        looped = anim is not None and anim.loop in ("loop", "pingPong") and remap is None
        if remap is None and not looped:
            # Rive shows a nested artboard for as long as its parent: a static one (no animation → a 1-frame comp) or a
            # oneShot that ended holds its last frame. AE drops the layer when its shorter source ends (the trailer's
            # Letterbox showed on the first frame of each shot only) → time remap, layer stretched to the parent.
            # (hold the LAST REAL frame, duration − 1 frame: AE's default end key sits AT the duration, where a 1-frame
            # comp shows nothing — measured on the Letterbox)
            J(f'if ({var}.outPoint < {ctx.comp}.duration - 1e-6) {{ if (!{var}.timeRemapEnabled) {var}.timeRemapEnabled = true; '
              f'var lst = Math.max(0, {var}.source.duration - {var}.source.frameDuration); remapSet({var}.property("ADBE Time Remapping"), [[0, 0], [lst, lst]]); '
              f'{var}.outPoint = {ctx.comp}.duration; }}')
        if looped:
            mode = "cycle" if anim.loop == "loop" else "pingpong"
            loop_expr = js('loopOut("%s")' % mode)
            J(f'if (!{var}.timeRemapEnabled) {{ {var}.timeRemapEnabled = true; }} {var}.property("ADBE Time Remapping").expression = {loop_expr}; {var}.outPoint = {ctx.comp}.duration;')
            self.rep.add(ctx.artboard.name, "converted", el, f"looping animation → time remap loopOut(\"{mode}\")")
        return var

    # ---- Image (+ mesh)
    def emit_image(self, el, parent, ctx):
        J = self.W
        ft = self.footage.get(el.get("assetId"))
        if not ft:
            self.rep.add(ctx.artboard.name, "unsupported", el, "image asset missing")
            return None
        mesh = el.find("Mesh")
        if mesh is not None:
            return self.emit_mesh(el, parent, ctx, mesh, ft)
        ka = ctx.keys(el.id, "assetId")
        if ka:
            # Image.assetId is bindable but not animatable: the Rive runtime ignores these keys, so does the conversion
            self.rep.add(ctx.artboard.name, "approx", el, f"{len(ka)} keys on assetId ignored (not animatable in Rive; bind it to a ViewModel image instead)")
            ka = None
        assets = [el.get("assetId")] + ([v for _, v, _, _ in ka] if ka else [])
        seen, order = set(), []
        for a in assets:
            if a and a not in seen and a in self.footage:
                seen.add(a); order.append(a)
        first = None
        for aid in order:
            var = J.var("img")
            J(f'var {var} = {ctx.comp}.layers.add({self.footage[aid]}); {var}.name = {js(el.name if len(order) == 1 else el.name + " · " + self.p.assets[aid].name)};')
            if parent:
                J(f"{var}.parent = {parent};")
            ox, oy = el.num("originX", 0.5), el.num("originY", 0.5)
            J(f'tr({var}, "ADBE Anchor Point").setValue([{js(ox)} * {var}.source.width, {js(oy)} * {var}.source.height]);')
            self.base_transform(var, el, ctx)
            if ka:
                # visible only while this asset is the keyed one (image swap keyframes)
                rows = ([[0, 100 if el.get("assetId") == aid else 0, "h", 0, 0, 0, 0]] if ka[0][0] > 0 else []) + [[f * ctx.fscale, 100 if v == aid else 0, "h", 0, 0, 0, 0] for f, v, _, _ in ka]
                swap_expr = 'value * effect("asset swap")("ADBE Slider Control-0001") / 100'
                J(f'var swp = slider({var}, "asset swap", 100); keys(swp, {js(rows)}); tr({var}, "ADBE Opacity").expression = {js(swap_expr)};')
            first = first or var
        if ka:
            self.rep.add(ctx.artboard.name, "converted", el, f"image swaps ({len(order)} assets) → one layer per asset, shown by hold keys")
        return first

    def emit_mesh(self, el, parent, ctx, mesh, ft):
        """Mesh on an image: a regular u/v grid -> one strip per row driven by Corner Pin (exact on the row edges)."""
        J = self.W
        ab = ctx.artboard.name
        verts = [v for v in mesh.children if v.tag in ("MeshVertex", "ContourMeshVertex")]
        us = sorted(set(round(v.num("u"), 5) for v in verts))
        vs = sorted(set(round(v.num("v"), 5) for v in verts))
        grid = {(round(v.num("u"), 5), round(v.num("v"), 5)): v for v in verts}
        if len(us) * len(vs) != len(verts) or any((u, w) not in grid for u in us for w in vs):
            self.rep.add(ab, "approx", el, f"mesh with {len(verts)} vertices is not a regular grid: drawn without deformation")
            e2 = El(el.tag, el.attrs, el.parent)
            e2.id = el.id
            return self.emit_image(e2, parent, ctx)
        try:
            from PIL import Image
            src = Image.open(os.path.join(self.p.dir, self.p.assets[el.get("assetId")].get("file"))).convert("RGBA")
        except Exception as e:
            self.rep.add(ab, "unsupported", el, f"mesh needs PIL to slice the image ({e})")
            return self.emit_image(El(el.tag, el.attrs, el.parent), parent, ctx)
        iw, ih = src.size
        mv = J.var("mesh")
        J(f"var {mv} = null;")
        J("if (RIVE_SHADER) {")
        ok = self.emit_mesh_shader(el, parent, ctx, mesh, ft, iw, ih, mv)
        J("} else {")
        if ok:
            self.rep.add(ab, "info", el, "without the Rive Shader plugin: row strips + Corner Pin instead (below)")
        x, y = el.num("x"), el.num("y")
        sx, sy = el.num("scaleX", 1), el.num("scaleY", 1)
        cols, rows = len(us) - 1, len(vs) - 1
        frames = sorted({k[0] for u in us for w in vs for k in (ctx.keys(grid[(u, w)].id, "x") or [])} | {0})
        kinds = {}
        for k in (ctx.keys(grid[(us[0], vs[0])].id, "x") or []):
            kinds[k[0]] = kind_of(k[2], k[3])

        def vert_at(u, w, f):
            v = grid[(u, w)]
            kx, ky = ctx.keys(v.id, "x"), ctx.keys(v.id, "y")
            return (value_at(kx, f) if kx else v.num("x"), value_at(ky, f) if ky else v.num("y"))

        def corner(u, vpx, f):
            jr = vpx / ih
            j0 = max(0, min(len(vs) - 2, max(i for i in range(len(vs)) if vs[i] <= jr + 1e-9)))
            t = (jr - vs[j0]) / max(1e-9, vs[j0 + 1] - vs[j0])
            (xa, ya), (xb, yb) = vert_at(u, vs[j0], f), vert_at(u, vs[j0 + 1], f)
            return [x + sx * (xa + (xb - xa) * t), y + sy * (ya + (yb - ya) * t)]
        first = None
        fname = os.path.splitext(os.path.basename(self.p.assets[el.get("assetId")].get("file")))[0]
        for j in range(rows):
            ya, yb = int(round(vs[j] * ih)), min(ih, int(round(vs[j + 1] * ih)) + 1)
            path = os.path.join(self.out_dir, f"{self.p.name}_{fname}_row{j}.png")
            src.crop((0, ya, iw, yb)).save(path)
            var = J.var("row")
            J(f'var {var}F = importFile({js(path)}); {var}F.parentFolder = FOOT; var {var} = {ctx.comp}.layers.add({var}F); {var}.name = {js(f"{el.name} row {j + 1}")};')
            if parent:
                J(f"{var}.parent = {parent};")
            J(f'resetTf({var}, 0, 0, 0, 0);')
            J(f'var cp = {var}.property("ADBE Effect Parade").addProperty("ADBE Corner Pin");')
            for slot, (u, yy) in (("0001", (us[0], ya)), ("0002", (us[-1], ya)), ("0003", (us[0], yb)), ("0004", (us[-1], yb))):
                rws = [[f * ctx.fscale, corner(u, yy, f)] + list(kinds.get(f, ("l", 0, 0, 0, 0))) for f in frames]
                J(f'keys(cp.property("ADBE Corner Pin-{slot}"), {js(rws)});')
            first = first or var
        self.rep.add(ab, "approx", el, f"mesh {cols}x{rows}: {rows} row strips + Corner Pin (exact on row edges, bilinear inside)")
        J(f"{mv} = {first};")
        J("}")
        return mv

    def emit_mesh_shader(self, el, parent, ctx, mesh, ft, iw, ih, mv):
        """Mesh on an image -> EXACT deformation with the Rive Shader plugin: a generated WGSL draws the triangles
        (Rive's per-triangle affine texture mapping, painter's order) and reads the vertex positions of every comp
        frame from a data image (16 bits per coordinate) plugged into its Texture 1. The image sits in a precomp large
        enough for every pose. Returns False (nothing written) when the mesh cannot be read."""
        import base64
        import struct
        J = self.W
        ab = ctx.artboard
        verts = [v for v in mesh.children if v.tag in ("MeshVertex", "ContourMeshVertex")]
        nv = len(verts)
        try:
            raw = base64.b64decode(mesh.get("triangleIndexBytes") or "")
        except Exception:
            raw = b""
        idx = list(raw) if nv <= 255 else list(struct.unpack("<%dH" % (len(raw) // 2), raw[: len(raw) // 2 * 2]))
        if nv < 3 or len(idx) < 3 or max(idx) >= nv:
            self.rep.add(ab.name, "approx", el, "mesh triangles unreadable: row strips only")
            J(f"{mv} = null;")
            return False
        idx = idx[: len(idx) // 3 * 3]
        ox, oy = -el.num("originX", 0.5) * iw, -el.num("originY", 0.5) * ih      # image top-left, image-local
        n = max(1, int(round(self.duration_of(ab, ctx.anim) * self.fps))) + 1
        fs = ctx.fscale or 1.0
        keyed = [(ctx.keys(v.id, "x"), ctx.keys(v.id, "y")) for v in verts]
        pos = []
        for fc in range(n):
            fa = fc / fs
            pos.append([(value_at(kx, fa) if kx else v.num("x"), value_at(ky, fa) if ky else v.num("y"))
                        for v, (kx, ky) in zip(verts, keyed)])
        xs = [p[0] for row in pos for p in row] + [ox, ox + iw]
        ys = [p[1] for row in pos for p in row] + [oy, oy + ih]
        minx, miny = math.floor(min(xs)) - 2, math.floor(min(ys)) - 2
        W, H = int(math.ceil(max(xs) - minx)) + 2, int(math.ceil(max(ys) - miny)) + 2
        # data image: row = comp frame, two pixels per vertex (x then y), R = high byte, G = low byte of round(c / W * 65535)
        from PIL import Image
        img = Image.new("RGBA", (2 * nv, n), (0, 0, 0, 255))
        px = img.load()
        for f, row in enumerate(pos):
            for i, (vx, vy) in enumerate(row):
                for k, (c, span) in enumerate(((vx - minx, W), (vy - miny, H))):
                    q = max(0, min(65535, int(round(c / span * 65535))))
                    px[2 * i + k, f] = (q >> 8, q & 255, 0, 255)
        tag = re.sub(r"[^A-Za-z0-9_]+", "_", f"{ab.name}_{el.name}_{el.id}")
        data_png = os.path.join(self.out_dir, f"mesh_{tag}.png")
        img.save(data_png)
        tri = ", ".join(f"{i}u" for i in idx)
        uvs = ", ".join(f"vec2<f32>({v.num('u'):.6f}, {v.num('v'):.6f})" for v in verts)
        wgsl = f"""// generated by rml2ae: Rive image mesh '{el.name}' ({nv} vertices, {len(idx) // 3} triangles), Rive Shader plugin
struct Params {{
    size: vec2<f32>,
    tick: f32,
    pad0: f32,
}};
@group(0) @binding(0) var srcTex: texture_2d<f32>;
@group(0) @binding(1) var srcSamp: sampler;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var vertTex: texture_2d<f32>;   // vertex positions per frame (data image)

const NV: u32 = {nv}u;
const NI: u32 = {len(idx)}u;
const SPAN = vec2<f32>({W}.0, {H}.0);
const IMG = vec4<f32>({ox - minx:.4f}, {oy - miny:.4f}, {iw}.0, {ih}.0);   // image rect in the precomp (left, top, w, h)
const TRI = array<u32, {len(idx)}>({tri});
const UV = array<vec2<f32>, {nv}>({uvs});

struct VSOut {{ @builtin(position) pos: vec4<f32>, @location(0) uv: vec2<f32> }};
@vertex
fn vs_main(@builtin(vertex_index) vid: u32) -> VSOut {{
    var positions = array<vec2<f32>, 3>(vec2<f32>(-1.0, -1.0), vec2<f32>(3.0, -1.0), vec2<f32>(-1.0, 3.0));
    var uvs = array<vec2<f32>, 3>(vec2<f32>(0.0, 1.0), vec2<f32>(2.0, 1.0), vec2<f32>(0.0, -1.0));
    var o: VSOut;
    o.pos = vec4<f32>(positions[vid], 0.0, 1.0);
    o.uv = uvs[vid];
    return o;
}}

fn vert(i: u32, row: i32) -> vec2<f32> {{
    let a = textureLoad(vertTex, vec2<i32>(i32(2u * i), row), 0);
    let b = textureLoad(vertTex, vec2<i32>(i32(2u * i + 1u), row), 0);
    let qx = round(a.r * 255.0) * 256.0 + round(a.g * 255.0);
    let qy = round(b.r * 255.0) * 256.0 + round(b.g * 255.0);
    return vec2<f32>(qx, qy) / 65535.0 * SPAN;
}}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {{
    let p = in.uv * P.size;
    let rows = i32(textureDimensions(vertTex).y);
    let row = clamp(i32(P.tick), 0, rows - 1);
    var hit = false;
    var tuv = vec2<f32>(0.0);
    for (var t: u32 = 0u; t < NI; t = t + 3u) {{
        let i0 = TRI[t];
        let i1 = TRI[t + 1u];
        let i2 = TRI[t + 2u];
        let a = vert(i0, row);
        let b = vert(i1, row);
        let c = vert(i2, row);
        let d = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y);
        if (abs(d) < 1e-9) {{ continue; }}
        let w0 = ((b.y - c.y) * (p.x - c.x) + (c.x - b.x) * (p.y - c.y)) / d;
        let w1 = ((c.y - a.y) * (p.x - c.x) + (a.x - c.x) * (p.y - c.y)) / d;
        let w2 = 1.0 - w0 - w1;
        if (w0 >= -1e-4 && w1 >= -1e-4 && w2 >= -1e-4) {{
            hit = true;                                     // painter's order: the last triangle drawn wins
            tuv = UV[i0] * w0 + UV[i1] * w1 + UV[i2] * w2;
        }}
    }}
    let src = (IMG.xy + tuv * IMG.zw) / P.size;
    let col = textureSampleLevel(srcTex, srcSamp, src, 0.0);
    if (!hit) {{ return vec4<f32>(0.0); }}
    return col;
}}
"""
        shader_path = os.path.join(self.out_dir, f"mesh_{tag}.wgsl")
        open(shader_path, "w", encoding="utf-8").write(wgsl)
        sid = RS.register(shader_path)
        pc = J.var("mpc")
        J(f'var {pc} = app.project.items.addComp({js(el.name + " (mesh)")}, {W}, {H}, 1, {js(max(n - 1, 1) / self.fps)}, FPS); {pc}.parentFolder = SUBS;')
        J(f'var mim = {pc}.layers.add({ft}); resetTf(mim, 0, 0, 0, 0); tr(mim, "ADBE Position").setValue([{js(ox - minx)}, {js(oy - miny)}]);')
        J(f'var {mv} = {ctx.comp}.layers.add({pc}); {mv}.name = {js(el.name)};')
        if parent:
            J(f"{mv}.parent = {parent};")
        self.base_transform(mv, el, ctx, anchor=[-minx, -miny])
        J(f'var mdl = {ctx.comp}.layers.add(importFile({js(data_png)})); mdl.name = {js(el.name + " · mesh data")}; mdl.enabled = false; mdl.moveToEnd();')
        J(f'var mfx = {mv}.property("ADBE Effect Parade").addProperty("{RS.MATCH_NAME}"); mfx.name = "Rive mesh"; mfx.property({RS.IDX_SHADER}).setValue({sid}); mfx.property({RS.IDX_TEX1}).setValue(mdl.index);')
        self.rep.add(ab.name, "converted", el, f"mesh ({nv} vertices, {len(idx) // 3} triangles) → Rive Shader plugin: exact per-triangle deformation, vertices per frame in a data image")
        return True

    # ---- Shape
    def path_shape(self, pp, offset=(0.0, 0.0), ctx=None, frame=None):
        """<PointsPath> -> JS shapeFrom(...) (vertex keys evaluated at `frame` when given)."""
        verts, ins, outs, radii = [], [], [], []
        ox, oy = offset
        for v in pp.children:
            if not is_a(v.tag, "Vertex"):
                continue
            radii.append(v.num("radius") if v.tag == "StraightVertex" else 0.0)
            def pv(name, default=0.0):
                k = ctx.keys(v.id, name) if (ctx is not None and frame is not None and v.id) else None
                return value_at(k, frame) if k else v.num(name, default)
            x, y = pv("x"), pv("y")
            verts.append([x + ox, y + oy])
            if v.tag == "CubicDetachedVertex":
                ir, idist = pv("inRotation"), pv("inDistance")
                orr, odist = pv("outRotation"), pv("outDistance")
                ins.append([math.cos(ir) * idist, math.sin(ir) * idist])
                outs.append([math.cos(orr) * odist, math.sin(orr) * odist])
            elif v.tag == "CubicMirroredVertex":
                r, d = pv("rotation"), pv("distance")
                ins.append([-math.cos(r) * d, -math.sin(r) * d])
                outs.append([math.cos(r) * d, math.sin(r) * d])
            elif v.tag == "CubicAsymmetricVertex":
                r, di, do = pv("rotation"), pv("inDistance"), pv("outDistance")
                ins.append([-math.cos(r) * di, -math.sin(r) * di])
                outs.append([math.cos(r) * do, math.sin(r) * do])
            else:
                ins.append([0, 0])
                outs.append([0, 0])
        closed = pp.get("isClosed", "true") == "true"
        if any(r > 0 for r in radii):
            verts, ins, outs = round_corners(verts, ins, outs, radii, closed)
        return f"shapeFrom({js(verts)}, {js(ins)}, {js(outs)}, {js(closed)})"

    # ---- feathered fills (Rive 1.3: a Fill with a Feather, fillRule clockwise) -> a solid cut by a feathered mask
    FEATHER_K = 1.25      # measured (AE 26.5 vs Rive 1.3, rect / ellipse / offset shadow): mask feather 1.25 × strength, no
                          # expansion -> 0.1 % mean error (expansion 0.33 × strength: 0.87 %)

    def _mask_shape(self, c, dx, dy):
        """a Path child -> JS shapeFrom(...) in the shape's space shifted by (dx, dy), or None"""
        x, y = c.num("x") + dx, c.num("y") + dy
        if c.tag == "PointsPath":
            return self.path_shape(c, (x, y))
        w, h = c.num("width"), c.num("height")
        cx, cy = x + (0.5 - c.num("originX", 0.5)) * w, y + (0.5 - c.num("originY", 0.5)) * h
        if c.tag == "Rectangle":
            V = [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]
            r = c.num("cornerRadiusTL", 0) or c.num("cornerRadius", 0)
            I = [[0, 0]] * 4
            O = [[0, 0]] * 4
            if r:
                V, I, O = round_corners(V, I, O, [r] * 4, True)
            return f"shapeFrom({js(V)}, {js(I)}, {js(O)}, true)"
        if c.tag == "Ellipse":
            k = 0.5522847498
            rx, ry = w / 2, h / 2
            V = [[cx, cy - ry], [cx + rx, cy], [cx, cy + ry], [cx - rx, cy]]
            I = [[-rx * k, 0], [0, -ry * k], [rx * k, 0], [0, ry * k]]
            O = [[rx * k, 0], [0, ry * k], [-rx * k, 0], [0, -ry * k]]
            return f"shapeFrom({js(V)}, {js(I)}, {js(O)}, true)"
        if c.tag == "Triangle":
            V = [[cx, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]
            return f"shapeFrom({js(V)}, {js([[0, 0]] * 3)}, {js([[0, 0]] * 3)}, true)"
        return None

    def emit_feather(self, el, paint, parent, ctx, tag_suffix="+feather"):
        """a feathered Fill -> a solid of its colour, cut by one mask per path of the shape, feathered as Rive (mask
        feather 1.25 × strength: 0.1 % mean error on a feathered card shadow, glow and moving dot), with the
        shape's transform. -> the layer var"""
        J = self.W
        ab = ctx.artboard.name
        fe = paint.find("Feather")
        st = fe.num("strength", 12)
        fx, fy = fe.num("offsetX"), fe.num("offsetY")
        rgb, a, kc, _ko = self.paint_color(paint, ctx, el)
        paths = [c for c in el.children if is_a(c.tag, "Path")]
        ext = 0.0
        for c in el.iter():
            for k in ("x", "y"):
                ext = max(ext, abs(c.num(k)))
            ext = max(ext, c.num("width") + abs(c.num("x")), c.num("height") + abs(c.num("y")))
        size = int(min(30000, max(64, 2 * (ext + 4 * st + abs(fx) + abs(fy)) + 64)))
        var = J.var("fth")
        name = el.name + (" · feather" if tag_suffix else "")
        J(f'var {var} = {ctx.comp}.layers.addSolid({js(rgb)}, {js(name)}, {size}, {size}, 1); {var}.name = {js(name)};')
        if parent:
            J(f"{var}.parent = {parent};")
        J(f'tr({var}, "ADBE Anchor Point").setValue([{size / 2}, {size / 2}]);')
        inner = fe.get("inner") == "true"
        n = 0
        for c in paths:
            sh = self._mask_shape(c, size / 2 + fx, size / 2 + fy)
            if sh is None:
                self.rep.add(ab, "approx", c, f"{c.tag} in a feathered fill: not in the mask")
                continue
            J(f'var fm = {var}.property("ADBE Mask Parade").addProperty("ADBE Mask Atom"); fm.property("ADBE Mask Shape").setValue({sh});')
            J(f'fm.property("ADBE Mask Feather").setValue([{js(self.FEATHER_K * st)}, {js(self.FEATHER_K * st)}]); ')
            if any(ctx.keys(v.id, pn) for v in c.children if v.id for pn in ("x", "y")):
                self.rep.add(ab, "approx", c, "animated path in a feathered fill: the mask keeps its first shape")
            n += 1
        self.base_transform(var, el, ctx)
        if a < 0.999:
            J(f'var __o = tr({var}, "ADBE Opacity"); if (__o.numKeys == 0) __o.setValue(__o.value * {js(a)});')
        if kc:
            self.rep.add(ab, "approx", el, "feathered fill with an animated colour: its first colour is kept")
        if tag_suffix:
            J(f'{var}.comment = {js(self.tag_of(el) + tag_suffix)};')
        self.rep.add(ab, "approx" if inner else "converted", el,
                     f"feathered fill (strength {st:g}{', inner' if inner else ''}) → solid + feathered mask{'s' if n > 1 else ''}"
                     + (" (inner feather ≈ inward mask feather)" if inner else ""))
        return var

    def emit_shape(self, el, parent, ctx, as_matte=False):
        J = self.W
        ab = ctx.artboard.name
        skinned = RIG.has_skin(el)
        feathered = [] if as_matte or skinned else [c for c in el.children if c.tag == "Fill" and c.find("Feather") is not None
                                                     and self.gradient_of(c) is None]
        if feathered and not any(c.tag in ("Fill", "Stroke") and c not in feathered for c in el.children):
            # only feathered fills: the solid IS the element's layer
            v = None
            for k, f in enumerate(feathered):
                v2 = self.emit_feather(el, f, parent, ctx, tag_suffix=("" if k == 0 else f"+feather{k}"))
                v = v or v2
            return v
        var = J.var("shp")
        J(f'var {var} = {ctx.comp}.layers.addShape(); {var}.name = {js(el.name + (" (matte)" if as_matte else "") + (" [skinned]" if skinned else ""))};')
        if parent and not skinned:
            J(f"{var}.parent = {parent};")
        J(f'var gr = {var}.property("ADBE Root Vectors Group");')
        # the group carries the element's name (AE's default "Groupe 1" told nothing; audit F35) — pull maps groups by rank
        J(f'var g = gr.addProperty("ADBE Vector Group"); g.name = {js(el.name or "Shape")}; var gc = g.property("ADBE Vectors Group");')
        # paths
        for c in el.children:
            if is_a(c.tag, "Path"):
                if skinned:
                    self.emit_baked_path(c, ctx, el)
                else:
                    self.emit_path(c, ctx, el)
        # paints: Rive draws the LATER paint on top, AE the item HIGHER in the group (added earlier) -> reversed order
        for c in reversed(el.children):
            if c.tag == "Fill" and c not in feathered:
                self.emit_fill(c, ctx, el, as_matte)
            elif c.tag == "Stroke":
                self.emit_stroke(c, ctx, el)
        fst = [c for c in el.children if c.tag == "Stroke" and c.find("Feather") is not None]
        if fst and not as_matte and not any(c.tag == "Fill" for c in el.children):
            # a feathered stroke alone on its shape (a glowing outline): the layer is blurred instead
            fs = max(f.find("Feather").num("strength", 0) for f in fst)
            J(f'var gbl = {var}.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2"); gbl.property("ADBE Gaussian Blur 2-0001").setValue({js(round(fs, 2))});')
            self.rep.add(ab, "approx", el, f"feathered stroke (strength {fs:g}) → Gaussian Blur {fs:g} on the layer")
        if as_matte and not any(c.tag in ("Fill", "Stroke") for c in el.children):
            # a paint-less clip source (Rive clips by geometry, draws nothing): the matte needs an opaque fill
            J('var fl = gc.addProperty("ADBE Vector Graphic - Fill"); fl.property("ADBE Vector Fill Color").setValue([1,1,1]); fl.property("ADBE Vector Fill Opacity").setValue(100);')
        grads = [c for c in el.children if c.tag == "Fill" and self.gradient_of(c) is not None]
        if grads:
            # effect points on a shape layer are content coordinates + the layer position (measured in AE 2026)
            self.emit_gradient(var, self.gradient_of(grads[0]), ctx, el, offset=((0.0, 0.0) if skinned else (el.num("x"), el.num("y"))))
            if len(grads) > 1:
                self.rep.add(ab, "approx", el, f"{len(grads)} gradient fills on one shape: only the first is a gradient")
        if skinned:
            # world-space geometry baked per frame: the layer stays at identity, unparented
            J(f'resetTf({var}, 0, 0, 0, 0);')
            if el.id:
                J(f'{var}.comment = {js("rive:" + el.id)};')
            b = blend_name(el.get("blendModeValue"))
            if b and b != "srcOver":
                J(f'{var}.blendingMode = BLEND[{js(b)}];')
            ko = ctx.keys(el.id, "opacity")
            if ko:
                J(f'keys(tr({var}, "ADBE Opacity"), {ctx.rows(ko, lambda v: v * 100)});')
            self.rep.add(ab, "converted", el, "skinned path → path keyframes baked per frame (linear blend skinning, world space)")
            return var
        J(f'tr({var}, "ADBE Anchor Point").setValue([0, 0]);')
        self.base_transform(var, el, ctx)
        if feathered:
            paints = [c for c in el.children if c.tag in ("Fill", "Stroke")]
            for k, f in enumerate(feathered):
                fv = self.emit_feather(el, f, parent, ctx, tag_suffix=f"+feather{k}")
                # Rive: a later paint draws on top — a feathered fill under the crisp paints goes under the layer
                below = paints.index(f) < max(paints.index(c) for c in paints if c not in feathered)
                J(f"{fv}.{'moveAfter' if below else 'moveBefore'}({var});")
            if el.id:
                self.layer_of[el.id] = var
        return var

    def emit_baked_path(self, c, ctx, shape):
        """Path of a skinned shape: one path keyframe per frame, in artboard space (skin or plain world transform)."""
        J = self.W
        if not c.tag == "PointsPath":
            self.rep.add(ctx.artboard.name, "approx", c, f"{c.tag} inside a skinned shape: not baked")
            return
        n = int(round(self.duration_of(ctx.artboard, ctx.anim) * (ctx.anim.fps if ctx.anim else self.fps)))
        frames = list(range(0, max(n, 1) + 1))
        skin = c.find("Skin")
        J(f'var pth = gc.addProperty("ADBE Vector Shape - Group"); pth.name = {js(c.name)};')
        closed = c.get("isClosed", "true") == "true"
        rows = []
        if skin is not None:
            for f, (V, I, O) in zip(frames, RIG.bake_skinned_path(c, skin, ctx, frames)):
                rows.append(f"[{js(f * ctx.fscale)}, shapeFrom({js(V)}, {js(I)}, {js(O)}, {js(closed)}), \"l\"]")
        else:
            cache = {}
            for f in frames:
                m = RIG.world_at(c, f, ctx, cache)
                V, I, O = self.path_points(c, ctx, f)
                Vw = [list(RIG.apply(m, x, y)) for x, y in V]
                Iw = [[m[0] * a + m[2] * b, m[1] * a + m[3] * b] for a, b in I]
                Ow = [[m[0] * a + m[2] * b, m[1] * a + m[3] * b] for a, b in O]
                rows.append(f"[{js(f * ctx.fscale)}, shapeFrom({js(Vw)}, {js(Iw)}, {js(Ow)}, {js(closed)}), \"l\"]")
        J(f'keys(pth.property("ADBE Vector Shape"), [{",".join(rows)}]);')

    def path_points(self, pp, ctx, frame):
        """Numeric (vertices, in tangents, out tangents) of a PointsPath at a frame (local space, radii baked)."""
        verts, ins, outs, radii = [], [], [], []
        for v in pp.children:
            if not is_a(v.tag, "Vertex"):
                continue
            radii.append(v.num("radius") if v.tag == "StraightVertex" else 0.0)
            x, y = v.num("x"), v.num("y")
            kx, ky = (ctx.keys(v.id, "x"), ctx.keys(v.id, "y")) if v.id else (None, None)
            if kx:
                x = value_at(kx, frame)
            if ky:
                y = value_at(ky, frame)
            verts.append([x, y])
            if v.tag == "CubicDetachedVertex":
                ir, idist, orr, odist = v.num("inRotation"), v.num("inDistance"), v.num("outRotation"), v.num("outDistance")
                ins.append([math.cos(ir) * idist, math.sin(ir) * idist]); outs.append([math.cos(orr) * odist, math.sin(orr) * odist])
            elif v.tag == "CubicMirroredVertex":
                r, d = v.num("rotation"), v.num("distance")
                ins.append([-math.cos(r) * d, -math.sin(r) * d]); outs.append([math.cos(r) * d, math.sin(r) * d])
            elif v.tag == "CubicAsymmetricVertex":
                r, di, do = v.num("rotation"), v.num("inDistance"), v.num("outDistance")
                ins.append([-math.cos(r) * di, -math.sin(r) * di]); outs.append([math.cos(r) * do, math.sin(r) * do])
            else:
                ins.append([0, 0]); outs.append([0, 0])
        if any(r > 0 for r in radii):
            verts, ins, outs = round_corners(verts, ins, outs, radii, pp.get("isClosed", "true") == "true")
        return verts, ins, outs

    def emit_path(self, c, ctx, shape):
        J = self.W
        ab = ctx.artboard.name
        x, y = c.num("x"), c.num("y")
        rot = c.num("rotation") * 180 / math.pi
        t = c.tag
        if t == "PointsPath":
            TANG = ("x", "y", "inRotation", "inDistance", "outRotation", "outDistance", "rotation", "distance", "radius")
            keyed = [v for v in c.children if is_a(v.tag, "Vertex") and any(ctx.keys(v.id, pn) for pn in TANG)]
            J(f'var pth = gc.addProperty("ADBE Vector Shape - Group"); pth.name = {js(c.name)};')
            if keyed:
                frames = sorted({k[0] for v in keyed for p in TANG for k in (ctx.keys(v.id, p) or [])})
                k0 = next(ctx.keys(keyed[0].id, pn) for pn in TANG if ctx.keys(keyed[0].id, pn))
                kinds = {k[0]: kind_of(k[2], k[3]) for k in k0}
                rws = ",".join(f"[{js(f * ctx.fscale)}, {self.path_shape(c, (x, y), ctx, f)}, {js(kinds.get(f, ('l', 0, 0, 0, 0))[0])}]" for f in frames)
                J(f'keys(pth.property("ADBE Vector Shape"), [{rws}]);')
                self.rep.add(ab, "converted", c, f"path with {len(keyed)} animated vertices → path keyframes ({len(frames)} keys)")
            else:
                J(f'pth.property("ADBE Vector Shape").setValue({self.path_shape(c, (x, y))});')
            return
        w, h = c.num("width"), c.num("height")
        ox, oy = c.num("originX", 0.5), c.num("originY", 0.5)
        cx, cy = x + (0.5 - ox) * w, y + (0.5 - oy) * h         # AE parametric paths are centred on their position
        if t == "Rectangle":
            J(f'var rc = gc.addProperty("ADBE Vector Shape - Rect"); rc.name = {js(c.name)}; rc.property("ADBE Vector Rect Size").setValue([{js(w)}, {js(h)}]); rc.property("ADBE Vector Rect Position").setValue([{js(cx)}, {js(cy)}]);')
            r = c.num("cornerRadiusTL", 0) or c.num("cornerRadius", 0)
            if r:
                J(f'rc.property("ADBE Vector Rect Roundness").setValue({js(r)});')
            self.size_keys(c, ctx, "rc", "ADBE Vector Rect Size", w, h)
        elif t == "Ellipse":
            J(f'var ell = gc.addProperty("ADBE Vector Shape - Ellipse"); ell.name = {js(c.name)}; ell.property("ADBE Vector Ellipse Size").setValue([{js(w)}, {js(h)}]); ell.property("ADBE Vector Ellipse Position").setValue([{js(cx)}, {js(cy)}]);')
            self.size_keys(c, ctx, "ell", "ADBE Vector Ellipse Size", w, h)
        elif t in ("Polygon", "Star"):
            pts = int(c.num("points", 5))
            J(f'var ps = gc.addProperty("ADBE Vector Shape - Star"); ps.name = {js(c.name)}; ps.property("ADBE Vector Star Type").setValue({2 if t == "Polygon" else 1}); ps.property("ADBE Vector Star Points").setValue({pts});')
            J(f'ps.property("ADBE Vector Star Position").setValue([{js(cx)}, {js(cy)}]); ps.property("ADBE Vector Star Outer Radius").setValue({js(max(w, h) / 2)});')
            if t == "Star":
                J(f'ps.property("ADBE Vector Star Inner Radius").setValue({js(max(w, h) / 2 * c.num("innerRadius", 0.5))});')
            if abs(w - h) > 1e-6:
                self.rep.add(ab, "approx", c, f"{t} {w}x{h}: AE polystar is round (radius {max(w, h) / 2})")
        elif t == "Triangle":
            verts = [[cx, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]
            J(f'var pth = gc.addProperty("ADBE Vector Shape - Group"); pth.name = {js(c.name)}; pth.property("ADBE Vector Shape").setValue(shapeFrom({js(verts)}, {js([[0, 0]] * 3)}, {js([[0, 0]] * 3)}, true));')
        else:
            self.rep.add(ab, "unsupported", c, f"path type {t}")
        if rot:
            self.rep.add(ab, "approx", c, "rotation on a parametric path ignored")

    def size_keys(self, c, ctx, var, prop, w, h):
        kw, kh = ctx.keys(c.id, "width"), ctx.keys(c.id, "height")
        if kw or kh:
            kw = kw or [(0, w, "hold", None)]
            kh = kh or [(0, h, "hold", None)]
            self.W(f'keys({var}.property("{prop}"), {ctx.rows2(kw, kh, lambda a, b: [a, b])});')

    def gradient_of(self, paint):
        for gtag in ("LinearGradient", "RadialGradient"):
            gd = paint.find(gtag)
            if gd is not None:
                return gd
        return None

    def emit_gradient(self, layer_var, gd, ctx, el, offset=(0.0, 0.0)):
        """Gradient paint -> Gradient Ramp effect (2 stops, keyable) or a PNG matted by the shape (more stops)."""
        J = self.W
        ab = ctx.artboard.name
        stops = sorted(gd.findall("GradientStop"), key=lambda st: st.num("position"))
        radial = gd.tag == "RadialGradient"
        ox, oy = offset
        sx, sy, ex, ey = gd.num("startX") + ox, gd.num("startY") + oy, gd.num("endX") + ox, gd.num("endY") + oy
        alpha_grad = any(argb(st.get("colorValue"))[3] < 0.999 for st in stops)
        # Ramp effect points are COMP coordinates, untouched by the layer's parent (measured: a parented shape's ramp
        # stayed where the unparented one would be) -> only an unparented, unrotated, unscaled shape can use a Ramp
        parented = el.parent is not None and el.parent.tag != "Artboard"
        plain = abs(el.num("rotation")) < 1e-6 and abs(el.num("scaleX", 1) - 1) < 1e-6 and abs(el.num("scaleY", 1) - 1) < 1e-6
        if len(stops) <= 2 and not alpha_grad and not parented and plain:      # a Ramp has no alpha: transparent stops go through the PNG
            c0 = argb(stops[0].get("colorValue")) if stops else (0, 0, 0, 1)
            c1 = argb(stops[-1].get("colorValue")) if len(stops) > 1 else c0
            p0, p1 = (stops[0].num("position") if stops else 0.0), (stops[-1].num("position") if len(stops) > 1 else 1.0)
            ax, ay = ex - sx, ey - sy
            S = [sx + ax * p0, sy + ay * p0]
            E = [sx + ax * p1, sy + ay * p1]
            J(f'var gr8 = {layer_var}.property("ADBE Effect Parade").addProperty("ADBE Ramp"); gr8.name = {js("Gradient " + gd.name)};')
            J(f'gr8.property("ADBE Ramp-0001").setValue({js(S)}); gr8.property("ADBE Ramp-0002").setValue({js(list(c0[:3]))}); gr8.property("ADBE Ramp-0003").setValue({js(E)}); gr8.property("ADBE Ramp-0004").setValue({js(list(c1[:3]))}); gr8.property("ADBE Ramp-0005").setValue({2 if radial else 1});')
            for prop, slot, axis in (("startX", "0001", 0), ("startY", "0001", 1), ("endX", "0003", 0), ("endY", "0003", 1)):
                k = ctx.keys(gd.id, prop)
                if k:
                    fixed = (S if slot == "0001" else E)
                    rows = [[f * ctx.fscale, ([v + ox, fixed[1]] if axis == 0 else [fixed[0], v + oy])] + list(kind_of(i, e)) for f, v, i, e in k]
                    J(f'keys(gr8.property("ADBE Ramp-{slot}"), {js(rows)});')
                    self.rep.add(ab, "approx", el, f"gradient {prop} keyed: only that coordinate is animated on the ramp point")
            for st, slot in ((stops[0] if stops else None, "0002"), (stops[-1] if len(stops) > 1 else None, "0004")):
                kc = ctx.keys(st.id, "colorValue") if st is not None else None
                if kc:
                    J(f'keys(gr8.property("ADBE Ramp-{slot}"), {ctx.rows(kc, lambda v: [v[0], v[1], v[2], 1])});')
            if abs(c0[3] - 1) > 1e-3 or abs(c1[3] - 1) > 1e-3:
                J(f'tr({layer_var}, "ADBE Opacity").setValue({js(min(c0[3], c1[3]) * 100)});')
                self.rep.add(ab, "approx", el, "gradient stop alpha → layer opacity (min of the stops)")
            self.rep.add(ab, "converted", el, f"{gd.tag} ({len(stops)} stops) → Gradient Ramp effect on a white fill (keyable)")
            return
        try:
            from PIL import Image
            import numpy as np
            pts = []
            for c in el.iter():
                if is_a(c.tag, "Vertex"):
                    pts.append((c.num("x") + ox, c.num("y") + oy))
                elif c.tag in ("Rectangle", "Ellipse", "Polygon", "Star", "Triangle"):
                    w, h = c.num("width"), c.num("height")
                    cx, cy = c.num("x") + (0.5 - c.num("originX", 0.5)) * w + ox, c.num("y") + (0.5 - c.num("originY", 0.5)) * h + oy
                    pts += [(cx - w / 2, cy - h / 2), (cx + w / 2, cy + h / 2)]
            if not pts:
                raise ValueError("no geometry")
            # content coordinates (the image is parented to the shape layer): undo the effect-space offset
            pts = [(px - ox, py - oy) for px, py in pts]
            sx, sy, ex, ey = sx - ox, sy - oy, ex - ox, ey - oy
            x0, y0 = min(p[0] for p in pts) - 2, min(p[1] for p in pts) - 2
            x1, y1 = max(p[0] for p in pts) + 2, max(p[1] for p in pts) + 2
            W_, H_ = max(2, int(x1 - x0)), max(2, int(y1 - y0))
            yy, xx = np.mgrid[0:H_, 0:W_]
            px, py = xx + x0, yy + y0
            if radial:
                r = math.hypot(ex - sx, ey - sy) or 1.0
                t = np.hypot(px - sx, py - sy) / r
            else:
                ax, ay = ex - sx, ey - sy
                L = (ax * ax + ay * ay) or 1.0
                t = ((px - sx) * ax + (py - sy) * ay) / L
            t = np.clip(t, 0, 1)
            pos = [st.num("position") for st in stops]
            cols = [argb(st.get("colorValue")) for st in stops]
            img = np.zeros((H_, W_, 4), dtype=np.float32)
            for ch in range(4):
                img[..., ch] = np.interp(t, pos, [c[ch] for c in cols])
            self._grad_n = getattr(self, "_grad_n", 0) + 1
            path = os.path.join(self.out_dir, f"{self.p.name}_gradient_{re.sub(r'[^A-Za-z0-9]+', '_', gd.id or (gd.name + str(self._grad_n)))}.png")
            Image.fromarray((img * 255).astype(np.uint8), "RGBA").save(path)
            gv = J.var("gimg")
            J(f'var {gv}F = importFile({js(path)}); {gv}F.parentFolder = FOOT; var {gv} = {ctx.comp}.layers.add({gv}F); {gv}.name = {js("Gradient " + gd.name)};')
            J(f'{gv}.parent = {layer_var}; resetTf({gv}, 0, 0, {js(x0)}, {js(y0)}); {gv}.moveBefore({layer_var}); {gv}.setTrackMatte({layer_var}, TrackMatteType.ALPHA); {layer_var}.moveBefore({gv});')
            self.rep.add(ab, "approx", el, f"{gd.tag} with {len(stops)} stops → exact raster (PNG) matted by the shape (static)")
        except Exception as ex:
            self.rep.add(ab, "approx", el, f"{gd.tag} ({len(stops)} stops): first stop colour ({ex})")

    def paint_color(self, paint, ctx, el):
        """(rgb, alpha, color key rows or None, opacity rows or None) of a Fill/Stroke."""
        ab = ctx.artboard.name
        sc = paint.find("SolidColor")
        if sc is not None:
            r, g, b, a = argb(sc.get("colorValue"))
            kc = ctx.keys(sc.id, "colorValue")
            if kc:
                return ([r, g, b], a, ctx.rows(kc, lambda v: [v[0], v[1], v[2], 1]), ctx.rows(kc, lambda v: v[3] * 100))
            return ([r, g, b], a, None, None)
        gd = self.gradient_of(paint)
        if gd is not None:
            return ([1, 1, 1], 1.0, None, None)          # white carrier: the gradient is an effect on the layer (emit_gradient)
        return ([0, 0, 0], 1.0, None, None)

    def emit_fill(self, c, ctx, el, as_matte):
        J = self.W
        rgb, a, kc, ko = self.paint_color(c, ctx, el)
        if as_matte:
            a = 1.0
        rule = 2 if c.get("fillRule") == "evenOdd" else 1
        J('var fl = gc.addProperty("ADBE Vector Graphic - Fill");')
        J(f'fl.property("ADBE Vector Fill Color").setValue({js(rgb)}); fl.property("ADBE Vector Fill Opacity").setValue({js(a * 100)}); fl.property("ADBE Vector Fill Rule").setValue({rule});')
        if kc:
            J(f'keys(fl.property("ADBE Vector Fill Color"), {kc}); keys(fl.property("ADBE Vector Fill Opacity"), {ko});')
        if c.get("isVisible") == "false":
            J('fl.property("ADBE Vector Fill Opacity").setValue(0);')
        self.emit_binds(c, {"isVisible": ('fl.property("ADBE Vector Fill Opacity")', "%s")}, ctx)
        sc = c.find("SolidColor")
        if sc is not None:
            self.emit_binds(sc, {"colorValue": ('fl.property("ADBE Vector Fill Color")', "%s")}, ctx)

    def emit_stroke(self, c, ctx, el):
        J = self.W
        ab = ctx.artboard.name
        rgb, a, kc, ko = self.paint_color(c, ctx, el)
        J('var st = gc.addProperty("ADBE Vector Graphic - Stroke");')
        J(f'st.property("ADBE Vector Stroke Color").setValue({js(rgb)}); st.property("ADBE Vector Stroke Opacity").setValue({js(a * 100)});')
        J(f'st.property("ADBE Vector Stroke Width").setValue({js(c.num("thickness", 1))});')
        cap = {"butt": 1, "round": 2, "square": 3}.get(c.get("cap", "butt"), 1)
        join = {"miter": 1, "round": 2, "bevel": 3}.get(c.get("join", "miter"), 1)
        J(f'st.property("ADBE Vector Stroke Line Cap").setValue({cap}); st.property("ADBE Vector Stroke Line Join").setValue({join});')
        if kc:
            J(f'keys(st.property("ADBE Vector Stroke Color"), {kc}); keys(st.property("ADBE Vector Stroke Opacity"), {ko});')
        kt = ctx.keys(c.id, "thickness")
        if kt:
            J(f'keys(st.property("ADBE Vector Stroke Width"), {ctx.rows(kt)});')
        dp = c.find("DashPath")
        if dp is not None:
            dashes = [d.num("length") for d in dp.findall("Dash")]
            if dashes:
                J('var dsh = st.property("ADBE Vector Stroke Dashes");')
                J(f'dsh.addProperty("ADBE Vector Stroke Dash 1").setValue({js(dashes[0])}); dsh.addProperty("ADBE Vector Stroke Gap 1").setValue({js(dashes[1] if len(dashes) > 1 else dashes[0])});')
                if len(dashes) > 2:
                    J(f'dsh.addProperty("ADBE Vector Stroke Dash 2").setValue({js(dashes[2])}); dsh.addProperty("ADBE Vector Stroke Gap 2").setValue({js(dashes[3] if len(dashes) > 3 else dashes[2])});')
                if dp.num("offset"):
                    J(f'dsh.addProperty("ADBE Vector Stroke Offset").setValue({js(dp.num("offset"))});')
                ko = ctx.keys(dp.id, "offset")
                if ko:
                    J(f'keys(dsh.addProperty("ADBE Vector Stroke Offset"), {ctx.rows(ko)});')
                    if dp.get("offsetIsPercentage") == "true":
                        self.rep.add(ab, "approx", el, "dash offset keyed in % of the path: AE offset is in px")
        tp = c.find("TrimPath")
        if tp is not None:
            J('var trm = gc.addProperty("ADBE Vector Filter - Trim");')
            J(f'trm.property("ADBE Vector Trim Start").setValue({js(tp.num("start") * 100)}); trm.property("ADBE Vector Trim End").setValue({js(tp.num("end", 1) * 100)}); trm.property("ADBE Vector Trim Offset").setValue({js(tp.num("offset") * 360)});')
            for prop, ae, k in (("start", "ADBE Vector Trim Start", 100), ("end", "ADBE Vector Trim End", 100), ("offset", "ADBE Vector Trim Offset", 360)):
                kk = ctx.keys(tp.id, prop)
                if kk:
                    J(f'keys(trm.property("{ae}"), {ctx.rows(kk, lambda v, k=k: v * k)});')
            if tp.get("modeValue") == "synchronized":
                J('trm.property("ADBE Vector Trim Type").setValue(2);')
            self.emit_binds(tp, {"start": ('trm.property("ADBE Vector Trim Start")', "%s * 100"), "end": ('trm.property("ADBE Vector Trim End")', "%s * 100"), "offset": ('trm.property("ADBE Vector Trim Offset")', "%s * 360")}, ctx)
        if c.find("Feather") is not None and any(x.tag == "Fill" for x in el.children):
            self.warn(ab, "approx", el, "Feather on a stroke: not reproduced (add a Gaussian blur on the layer if wanted)")
        if c.get("isVisible") == "false":
            J('st.property("ADBE Vector Stroke Opacity").setValue(0);')
        self.emit_binds(c, {"isVisible": ('st.property("ADBE Vector Stroke Opacity")', "%s"), "thickness": ('st.property("ADBE Vector Stroke Width")', "%s")}, ctx)
        sc = c.find("SolidColor")
        if sc is not None:
            self.emit_binds(sc, {"colorValue": ('st.property("ADBE Vector Stroke Color")', "%s")}, ctx)

    # ---- Text
    def emit_text(self, el, parent, ctx):
        J = self.W
        ab = ctx.artboard.name
        style = el.find("TextStylePaint") or el.find("TextStyle")
        runs = el.findall("TextValueRun")
        if style is None or not runs:
            self.rep.add(ab, "unsupported", el, "text without style/run")
            return None
        if len(runs) > 1:
            self.rep.add(ab, "approx", el, f"{len(runs)} text runs: one AE text layer with the first run's style")
        text = "".join(r.get("text", "") for r in runs)
        size = style.num("fontSize", 12)
        lh = style.num("lineHeight", -1)
        tracking_px = style.num("letterSpacing", 0)
        ps, asc, desc, measure = self.fonts.get(style.get("fontAssetId"), ("Arial", ASCENT, 0.251, lambda t, s: len(t) * s * 0.6))
        fill = style.find("Fill")
        rgb, a = [0, 0, 0], 1.0
        if fill is not None:
            rgb, a, _, _ = self.paint_color(fill, ctx, el)
        align = el.get("alignValue", "left")
        just = {"left": "LEFT_JUSTIFY", "center": "CENTER_JUSTIFY", "right": "RIGHT_JUSTIFY"}.get(align, "LEFT_JUSTIFY")
        sizing = el.get("sizingValue", "autoWidth")
        width, height = el.num("width", 0), el.num("height", 0)
        lines = text.split("\n")
        n = len(lines)
        if el.get("overflowValue") == "fit" and width > 0:
            widest = max(measure(l, size) for l in lines) or 1.0
            if widest > width:
                size = size * width / widest
                self.rep.add(ab, "converted", el, f"overflow=fit → font size computed to fit the box ({size:.1f})")
        lh_eff = lh if lh > 0 else (asc + desc) * size
        box = sizing in ("autoHeight", "fixed") and width > 0
        if box:
            bw, bh = width, (height if sizing == "fixed" and height > 0 else (n - 1) * lh_eff + BLOCK_H * size)
        else:
            bw = max(measure(l, size) + tracking_px * max(len(l) - 1, 0) for l in lines)
            bh = (n - 1) * lh_eff + BLOCK_H * size
        var = J.var("txt")
        # AE tracking is in 1/1000 em and must be an INTEGER (measured: "53.333333 n'est pas un entier" otherwise)
        tracking = int(round(tracking_px / size * 1000)) if size else 0
        box_h = bh + size            # room for AE's own line layout; the box is centred on the layer origin (measured)
        J(f'var {var} = textLayer({ctx.comp}, {js(text)}, {js(ps)}, {js(size)}, {js(rgb)}, ParagraphJustification.{just}, {js(tracking)}, {js(lh if lh > 0 else 0)}, {js(bw if box else 0)}, {js(box_h if box else 0)}); {var}.name = {js(el.name)};')
        if a < 1:
            J(f'tr({var}, "ADBE Opacity").setValue({js(a * 100)});')
        stroke = style.find("Stroke")
        if stroke is not None:
            srgb, sa, _, _ = self.paint_color(stroke, ctx, el)
            fe = stroke.find("Feather")
            if fe is not None:
                J(f'var ds = {var}.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow"); ds.property("ADBE Drop Shadow-0002").setValue({js(sa * 255)}); ds.property("ADBE Drop Shadow-0003").setValue({js(180 if fe.num("offsetY") >= 0 else 0)}); ds.property("ADBE Drop Shadow-0004").setValue({js(abs(fe.num("offsetY")))}); ds.property("ADBE Drop Shadow-0005").setValue({js(fe.num("strength", 20))});')
                self.rep.add(ab, "approx", el, "feathered text stroke → Drop Shadow")
            else:
                J(f'textStroke({var}, {js(srgb)}, {js(stroke.num("thickness", 1))});')
        if parent:
            J(f"{var}.parent = {parent};")
        # anchor: Rive origin (fraction of the bounds) -> AE layer coords (box: top-left = 0,0 ; point: baseline origin)
        ox, oy = el.num("originX", 0), el.num("originY", 0)
        ax, ay = ox * bw, oy * bh
        if el.get("originValue") == "baseline":
            # Rive: the origin sits on the first baseline instead of the top of the bounds
            ay += asc * size
        if box:
            # AE centres the box on the layer origin and puts the first baseline lower than Rive: shift the box up
            ax, ay = ax - bw / 2, ay - box_h / 2 - (asc - AE_BOX_BASELINE) * size
        if not box:
            ay -= asc * size
            if align == "center":
                ax -= bw / 2
            elif align == "right":
                ax -= bw
        J(f'tr({var}, "ADBE Anchor Point").setValue([{js(ax)}, {js(ay)}]);')
        self.base_transform(var, el, ctx)
        if not box:
            # point text: the layer origin is the baseline; Rive's x,y is the top-left of the bounds
            pass
        for r in runs[:1]:
            self.emit_binds(r, {"text": (f'{var}.property("ADBE Text Properties").property("ADBE Text Document")', "%s")}, ctx)
            kt = ctx.keys(r.id, "text")
            if kt:
                rows = [[f * ctx.fscale, str(v)] for f, v, _, _ in kt]
                J(f'textKeys({var}, {js(rows)});')
                self.rep.add(ab, "converted", el, f"text changes over time ({len(kt)} keys) → source text hold keys")
        if len(runs) > 1:
            pos = 0
            for r in runs:
                st = self.p.by_id.get(r.get("styleId")) or style
                txt = r.get("text", "")
                if st is not None and st is not style and txt:
                    ps2 = self.fonts.get(st.get("fontAssetId"), (None, 0, 0, None))[0]
                    fill2 = st.find("Fill")
                    rgb2 = self.paint_color(fill2, ctx, el)[0] if fill2 is not None else None
                    J(f'runStyle({var}, {pos}, {len(txt)}, {js(ps2)}, {js(st.num("fontSize", 0))}, {js(rgb2)});')
                pos += len(txt)
        for prop in ("fontSize", "lineHeight"):
            if ctx.keys(style.id, prop):
                self.rep.add(ab, "unsupported", el, f"animated {prop}: AE source text cannot interpolate (not converted)")
        kl = ctx.keys(style.id, "letterSpacing")
        if kl and size:
            # animated letter spacing -> a text animator (no selector = all characters) whose Tracking Amount carries the
            # difference to the base tracking, keyed with the same frames and eases
            av, tv = J.var("tanim"), J.var("ttrk")
            J(f'var {av} = {var}.property("ADBE Text Properties").property("ADBE Text Animators").addProperty("ADBE Text Animator"); {av}.name = "Tracking";')
            J(f'var {tv} = {av}.property("ADBE Text Animator Properties").addProperty("ADBE Text Tracking Amount");')
            # measured (AE 2026, sizes 40 and 60): an animator's Tracking Amount is ~1 px per unit — NOT the 1/1000 em
            # of TextDocument.tracking — so it carries the difference in px
            J(f'keys({tv}, {ctx.rows(kl, lambda v, b=tracking_px: v - b)});')
            self.rep.add(ab, "converted", el, f"animated letterSpacing ({len(kl)} keys) → text animator Tracking Amount")
        # modifiers -> animators
        for gi, g in enumerate(el.findall("TextModifierGroup")):
            self.emit_animator(g, gi, var, ctx, el)
        return var

    def emit_animator(self, g, gi, var, ctx, el):
        J = self.W
        ab = ctx.artboard.name
        rng = g.find("TextModifierRange")
        props = {}
        flags = g.get("modifierFlags")
        if g.get("modifyTranslation") == "true" or g.get("x") or g.get("y"):
            props["pos"] = [g.num("x"), g.num("y")]
        if g.get("modifyRotation") == "true" or g.get("rotation"):
            props["rot"] = g.num("rotation") * 180 / math.pi
        if g.get("modifyOpacity") == "true":
            props["opacity"] = g.num("opacity", 1) * 100
        if g.get("modifyScale") == "true" or g.get("scaleX") or g.get("scaleY"):
            props["scale"] = [g.num("scaleX", 1) * 100, g.num("scaleY", 1) * 100]
        if not props:
            self.rep.add(ab, "approx", el, f"modifier group '{g.name}' modifies nothing AE can animate (flags {flags})")
            return
        units = rng.get("unitsValue", "characters") if rng is not None else "characters"
        index = (rng.get("typeValue") == "unitIndex") if rng is not None else False
        f0 = rng.num("modifyFrom", 0) if rng is not None else 0
        f1 = rng.num("modifyTo", 1) if rng is not None else 1
        sel = {"units": units if units in ("words", "lines", "characters") else "characters", "index": index,
               "start": f0 if index else f0 * 100, "end": f1 if index else f1 * 100}
        an = J.var("an")
        pj = ", ".join(f"{k}: {js(v)}" for k, v in props.items())
        J(f'var {an} = animator({var}, {js(g.name)}, {{{pj}}}, {js(sel)});')
        if rng is not None:
            ks = ctx.keys(rng.id, "strength")
            J(f'{an}.amount.setValue({js(rng.num("strength", 1) * 100)});')
            if ks:
                J(f'keys({an}.amount, {ctx.rows(ks, lambda v: v * 100)});')
            for prop, key in (("modifyFrom", "start"), ("modifyTo", "end")):
                kk = ctx.keys(rng.id, prop)
                if kk:
                    J(f'keys({an}.{key}, {ctx.rows(kk, (lambda v: v) if index else (lambda v: v * 100))});')
            self.emit_binds(rng, {"strength": (f'{an}.amount', "%s * 100")}, ctx)
            if rng.get("falloffFrom") or rng.get("falloffTo"):
                self.warn(ab, "approx", el, "range falloff (falloffFrom/To) not reproduced in the AE selector")
        for prop, key, fn in (("x", "pos", None), ("y", "pos", None), ("rotation", "rot", lambda v: v * 180 / math.pi), ("opacity", "opacity", lambda v: v * 100)):
            kk = ctx.keys(g.id, prop)
            if kk and key == "pos":
                kx, ky = ctx.keys(g.id, "x") or [(0, g.num("x"), "hold", None)], ctx.keys(g.id, "y") or [(0, g.num("y"), "hold", None)]
                J(f'keys({an}.pos, {ctx.rows2(kx, ky, lambda a, b: [a, b, 0])});')
            elif kk and key in props:
                J(f'keys({an}.{key}, {ctx.rows(kk, fn)});')
        self.rep.add(ab, "converted", el, f"modifier '{g.name}' → text animator ({', '.join(props)}; {units} {'index' if index else '%'} {f0}-{f1})")

    def warn(self, ab, kind, el, msg):
        k = (ab, msg)
        if k not in self.warned:
            self.warned.add(k)
            self.rep.add(ab, kind, el, msg)
