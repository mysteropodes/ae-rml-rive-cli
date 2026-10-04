"""Replay a scripted element as a PNG sequence rendered by the Rive CLI.

The CLI screenshots composite on an opaque background, so the element is rendered twice — artboard fill black and
white — and un-matted (straight alpha: a = 1 - (W - B)/255, rgb = B / a). Only the element and its ancestors are
kept in a temporary copy of the project, so the render is the element alone, in world space (the ancestors' keyed
transforms — cameras — are baked in).
"""
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

_AMP = re.compile(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")


def isolate_rml(text, artboard_name, element_id, fill_hex, animation_id=None):
    """scene.rml text with, inside `artboard_name`, only the element `element_id` and its ancestors (drawable siblings
    removed, keys/clips that pointed at removed objects dropped), and the artboard background = solid `fill_hex`."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(_AMP.sub("&amp;", text))
    for ab in root:
        if ab.tag != "Artboard" or ab.get("name") != artboard_name:
            continue
        parent = {c: p for p in ab.iter() for c in p}
        target = None
        for e in ab.iter():
            if e.get("id") == element_id:
                target = e
                break
        if target is None:
            raise ValueError(f"element {element_id} not in artboard {artboard_name}")
        keep = set()
        e = target
        while e is not None and e is not ab:
            keep.add(id(e))
            e = parent.get(e)
        # elements the subtree refers to by id (constraint targets, clip sources, bones of tendons...) stay, with their ancestors
        by_id = {x.get("id"): x for x in ab.iter() if x.get("id")}
        refs = set()
        for x in target.iter():
            for k, v in x.attrib.items():
                if k.endswith("Id") and k != "id" and v in by_id:
                    refs.add(v)
        for r in refs:
            e = by_id[r]
            while e is not None and e is not ab:
                keep.add(id(e))
                e = parent.get(e)
        keep_subtrees = {id(target)} | {id(by_id[r]) for r in refs}
        for f in ab.findall("Fill"):
            ab.remove(f)
        bg = ET.Element("Fill", {"name": "Replay background"})
        ET.SubElement(bg, "SolidColor", {"colorValue": fill_hex, "name": "C"})
        ab.insert(0, bg)
        drawable = ("Node", "Shape", "Image", "Text", "NestedArtboard", "ScriptedDrawable", "Solo", "LayoutComponent", "ScriptedLayout",
                    "Bone", "RootBone", "Joystick")

        def prune(node):
            for c in list(node):
                if id(c) in keep:
                    if id(c) not in keep_subtrees:
                        prune(c)
                elif c.tag in drawable or c.tag.startswith("Scripted"):
                    node.remove(c)
        prune(ab)
        if animation_id:
            # make the default state machine play exactly this animation (the CLI only plays the entry state)
            sm_id = ab.get("defaultStateMachineId")
            sms = [x for x in ab.findall("StateMachine")]
            sm = next((x for x in sms if x.get("id") == sm_id), sms[0] if sms else None)
            if sm is not None:
                for layer in sm.findall("StateMachineLayer"):
                    st = next((x for x in layer.findall("AnimationState") if x.get("animationId") == animation_id), None)
                    if st is None:
                        st = ET.SubElement(layer, "AnimationState", {"x": "800", "y": "0", "animationId": animation_id, "id": "0:999901"})
                    for tr in list(st.findall("StateTransition")):
                        st.remove(tr)
                    entry = layer.find("EntryState")
                    if entry is not None:
                        for tr in list(entry):
                            entry.remove(tr)
                        ET.SubElement(entry, "StateTransition", {"stateToId": st.get("id")})
                    break
        ids = {e.get("id") for e in ab.iter() if e.get("id")}
        for anim in ab.findall("LinearAnimation"):
            for ko in list(anim):
                if ko.tag == "KeyedObject" and ko.get("objectId") not in ids:
                    anim.remove(ko)
        for e in ab.iter():
            for c in list(e):
                if c.tag == "ClippingShape" and c.get("sourceId") not in ids:
                    e.remove(c)
                elif c.tag.endswith("Constraint") and c.get("targetId") and c.get("targetId") not in ids:
                    e.remove(c)
    return ET.tostring(root, encoding="unicode")


def render_sequence(project_dir, artboard_name, element_id, out_dir, n_frames, fps, jobs=8, log=print, animation_id=None):
    """-> list of RGBA PNG paths f0000.png... (cached when already rendered)."""
    os.makedirs(out_dir, exist_ok=True)
    paths = [os.path.join(out_dir, f"f{i:04d}.png") for i in range(n_frames)]
    if all(os.path.exists(p) for p in paths):
        return paths
    from PIL import Image
    import numpy as np
    tmp = tempfile.mkdtemp(prefix="rml2ae_replay_")
    src = open(os.path.join(project_dir, "scene.rml"), encoding="utf-8").read()
    for shade, hexv in (("black", "FF000000"), ("white", "FFFFFFFF")):
        d = os.path.join(tmp, shade)
        os.makedirs(d)
        for f in os.listdir(project_dir):
            p = os.path.join(project_dir, f)
            if os.path.isfile(p) and not f.endswith(".rml"):
                shutil.copy(p, d)
        open(os.path.join(d, "scene.rml"), "w", encoding="utf-8").write(isolate_rml(src, artboard_name, element_id, hexv, animation_id))
    env = {**os.environ, "RIVE_NO_TUI": "1"}

    def shot(args):
        shade, i = args
        t = i / fps + 0.012
        p = os.path.join(tmp, shade, f"f{i:04d}.png")
        subprocess.run(["rive", os.path.join(tmp, shade), f"--screenshot={p}", f"--artboard={artboard_name}", f"--advance={t:.4f}s", "--quiet"],
                       capture_output=True, text=True, env=env)
        return p
    log(f"replay: rendering {n_frames} frames x2 of '{artboard_name}' / {element_id}")
    # preflight: one build; a failing isolated project is reported once instead of failing 2 x n times
    r = subprocess.run(["rive", os.path.join(tmp, "black"), "--verify"], capture_output=True, text=True, env=env)
    if "error" in (r.stdout + r.stderr).lower() and "0 errors" not in (r.stdout + r.stderr):
        err = [l for l in (r.stdout + r.stderr).splitlines() if "error" in l.lower()][:3]
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("isolated project does not build: " + " | ".join(err))
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(shot, [(s, i) for i in range(n_frames) for s in ("black", "white")]))
    # the CLI occasionally drops a frame under load: retry the missing ones one by one
    for shade in ("black", "white"):
        for i in range(n_frames):
            if not os.path.exists(os.path.join(tmp, shade, f"f{i:04d}.png")):
                for _ in range(3):
                    shot((shade, i))
                    if os.path.exists(os.path.join(tmp, shade, f"f{i:04d}.png")):
                        break
    for i, out in enumerate(paths):
        pb, pw = os.path.join(tmp, "black", f"f{i:04d}.png"), os.path.join(tmp, "white", f"f{i:04d}.png")
        if not (os.path.exists(pb) and os.path.exists(pw)):
            log(f"replay: frame {i} missing")
            continue
        b = np.asarray(Image.open(pb).convert("RGB")).astype(np.float32)
        w = np.asarray(Image.open(pw).convert("RGB")).astype(np.float32)
        a = 1.0 - np.clip((w - b).mean(axis=2) / 255.0, 0, 1)
        rgb = np.where(a[..., None] > 1e-3, b / np.maximum(a[..., None], 1e-3), 0)
        rgba = np.dstack([np.clip(rgb, 0, 255), a * 255]).astype(np.uint8)
        Image.fromarray(rgba, "RGBA").save(out)
    shutil.rmtree(tmp, ignore_errors=True)
    return paths


def wgsl_params(path):
    """Field names of `struct Params { ... }` in a .wgsl file."""
    try:
        s = open(path, encoding="utf-8").read()
    except OSError:
        return []
    m = re.search(r"struct\s+Params\s*\{(.*?)\}", s, re.S)
    if not m:
        return []
    return re.findall(r"^\s*(\w+)\s*:", m.group(1), re.M)


def luau_defaults(path):
    """{vm property name: default} from `num(vm, "name", default)` calls in a Luau script (+ the shader it loads)."""
    try:
        s = open(path, encoding="utf-8").read()
    except OSError:
        return {}, None
    out = {n: float(v) for n, v in re.findall(r'num\(\s*\w+\s*,\s*"(\w+)"\s*,\s*([\d.]+)\s*\)', s)}
    sh = re.search(r'shader\(\s*"(\w+)"\s*\)', s)
    return out, (sh.group(1) if sh else None)
