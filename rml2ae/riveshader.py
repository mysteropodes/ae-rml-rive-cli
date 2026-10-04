"""Emit the "Rive Shader" After Effects plugin (rml2ae/plugin) for a Rive post-process script.

The plugin maps `struct Params` to fixed parameter slots (see plugin/src/RiveShader.h and RiveShader.cpp::mapParams —
keep this file in sync): host fields (size/tick/fxTick/seed/pad*) are filled by the plugin; vec2 -> Point slots;
vec3/vec4 whose comment mentions "color" -> Color slots; everything else -> generic sliders one scalar at a time;
textures at @binding(3+) -> Layer slots. Field values come from the Luau `buffer.writef32(b, offset, expr)` lines
(`num(vm, "name", default)` -> the ViewModel value, else the default), else the comment's `default x`.

The shader path is registered in the plugin's shaders.tsv (id = "Shader" slider value): macOS
~/Library/Application Support/RiveShader, Windows %APPDATA%\\RiveShader (aeapp.shader_registry()).
"""
import os
import re

from rml2ae import aeapp

MATCH_NAME = "RIVE RiveShader"
NUM_SLIDERS, NUM_COLORS, NUM_POINTS, NUM_TEXTURES = 16, 4, 4, 3
IDX_SHADER, IDX_STEP_FPS, IDX_FX_FPS, IDX_SEED = 1, 3, 4, 5
IDX_P1 = 6
IDX_C1 = IDX_P1 + NUM_SLIDERS          # 22
IDX_PT1 = IDX_C1 + NUM_COLORS          # 26
IDX_TEX1 = IDX_PT1 + NUM_POINTS        # 30
HOST = ("size", "tick", "fxTick", "seed")
TYPES = {"f32": (1, 4, 4), "i32": (1, 4, 4), "u32": (1, 4, 4), "vec2<f32>": (2, 8, 8), "vec3<f32>": (3, 12, 16), "vec4<f32>": (4, 16, 16),
         "vec2<i32>": (2, 8, 8), "vec2<u32>": (2, 8, 8), "vec2f": (2, 8, 8), "vec3f": (3, 12, 16), "vec4f": (4, 16, 16)}
REGISTRY = aeapp.shader_registry()


def is_host(name):
    return name in HOST or name.startswith("pad") or name.startswith("_pad")


def parse(path):
    """-> [dict(name, type, count, offset, comment, default, min, max)], [extra texture binding names]"""
    src = open(path, encoding="utf-8").read()
    fields, offset = [], 0
    m = re.search(r"struct\s+Params\s*\{([^}]*)\}", src)
    for line in (m.group(1).split("\n") if m else []):
        comment = ""
        if "//" in line:
            line, comment = line.split("//", 1)
            comment = comment.strip()
        line = line.strip().rstrip(",;").strip()
        fm = re.match(r"(\w+)\s*:\s*([\w<>]+)", line)
        if not fm:
            continue
        count, size, align = TYPES.get(fm.group(2), (1, 4, 4))
        while offset % align:
            offset += 1
        d = dict(name=fm.group(1), type=fm.group(2), count=count, offset=offset, comment=comment, default=None, min=0.0, max=1.0)
        r = re.search(r"(-?[\d.]+)\s*\.\.\s*(-?[\d.]+)", comment)
        if r:
            d["min"], d["max"] = float(r.group(1)), float(r.group(2))
        dm = re.search(r"default\s*[=:]?\s*(-?[\d.]+)", comment)
        if dm:
            d["default"] = float(dm.group(1))
        fields.append(d)
        offset += size
    textures = [n for g, b, kind, n in [(int(a), int(b), ("texture" if t.startswith("texture_") else "other"), n)
                for a, b, n, t in re.findall(r"@group\((\d+)\)\s*@binding\((\d+)\)\s*var\s+(\w+)\s*:\s*([\w<>]+)", src)] if kind == "texture"]
    return fields, textures[1:]


def mapping(fields, textures):
    """field name -> ("slider"|"color"|"point", first param index) ; texture name -> param index"""
    out, ns, nc, npnt = {}, 0, 0, 0
    for f in fields:
        if is_host(f["name"]):
            continue
        if f["count"] == 2 and "i32" not in f["type"] and "u32" not in f["type"] and npnt < NUM_POINTS:
            out[f["name"]] = ("point", IDX_PT1 + npnt); npnt += 1
        elif f["count"] >= 3 and any(k in f["comment"].lower() for k in ("color", "colour", "couleur")) and nc < NUM_COLORS:
            out[f["name"]] = ("color", IDX_C1 + nc); nc += 1
        elif ns + f["count"] <= NUM_SLIDERS:
            out[f["name"]] = ("slider", IDX_P1 + ns); ns += f["count"]
    tex = {n: IDX_TEX1 + i for i, n in enumerate(textures[:NUM_TEXTURES])}
    return out, tex


def luau_writes(luau_path, fields):
    """Per field: what the Luau writes into it -> dict(name -> ('vm', vmName, default) | ('clock', vmName, default) | ('const', v))"""
    try:
        src = open(luau_path, encoding="utf-8").read()
    except OSError:
        return {}
    by_offset = {f["offset"]: f["name"] for f in fields}
    out = {}
    for off, expr in re.findall(r"buffer\.writef32\(\s*\w+\s*,\s*(\d+)\s*,\s*(.+?)\)\s*(?:--.*)?$", src, re.M):
        name = by_offset.get(int(off))
        if not name:
            continue
        nm = re.search(r'num\(\s*[\w.]+\s*,\s*"(\w+)"\s*,\s*([\d.]+)\s*\)', expr)
        if nm and ("clock" in expr or "Fps" in nm.group(1)):
            out[name] = ("clock", nm.group(1), float(nm.group(2)))
        elif nm:
            out[name] = ("vm", nm.group(1), float(nm.group(2)))
        elif re.fullmatch(r"[\d.]+", expr.strip()):
            out[name] = ("const", float(expr))
    sf = re.search(r'num\(\s*[\w.]+\s*,\s*"(stepFps|fps|stepfps)"\s*,\s*([\d.]+)\s*\)', src)
    if sf:
        out["__stepFps"] = ("vm", sf.group(1), float(sf.group(2)))
    return out


def register(path):
    """Add `path` to the plugin registry (or find it); returns the id."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    entries = {}
    if os.path.exists(REGISTRY):
        for line in open(REGISTRY, encoding="utf-8"):
            if "\t" in line and not line.startswith("#"):
                i, p = line.rstrip("\n").split("\t", 1)
                if i.isdigit():
                    entries[int(i)] = p
    for i, p in entries.items():
        if p == path:
            return i
    new_id = max(entries) + 1 if entries else 1
    with open(REGISTRY, "a", encoding="utf-8") as f:
        f.write(f"{new_id}\t{path}\n")
    return new_id


def texture_params(shader_path):
    """texture binding name -> Layer param index (for the passes wiring)."""
    fields, textures = parse(shader_path)
    return mapping(fields, textures)[1]


def jsx_apply(layer_var, shader_path, luau_path, value_of, seed=1.0, passes=None):
    """JSX lines applying the plugin to `layer_var` with the shader's values; `value_of(vmName, default)` reads the
    ViewModel. Returns (lines, notes, step_fps)."""
    fields, textures = parse(shader_path)
    fmap, tmap = mapping(fields, textures)
    writes = luau_writes(luau_path, fields) if luau_path else {}
    sid = register(shader_path)
    L = [f'var rsfx = {layer_var}.property("ADBE Effect Parade").addProperty("{MATCH_NAME}"); rsfx.name = {_js(os.path.basename(shader_path))};',
         f'rsfx.property({IDX_SHADER}).setValue({sid}); rsfx.property({IDX_SEED}).setValue({seed});']
    notes, fx_fps, step_fps = [], None, None
    if "__stepFps" in writes:
        _, vmn, d = writes["__stepFps"]
        step_fps = value_of(vmn, d)
    for f in fields:
        name = f["name"]
        w = writes.get(name)
        if name == "fxTick" and w and w[0] == "clock":
            fx_fps = value_of(w[1], w[2])
            continue
        if name not in fmap:
            continue
        kind, idx = fmap[name]
        if w and w[0] == "vm":
            v = value_of(w[1], w[2]); src = f'VM {w[1]}'
        elif w and w[0] == "const":
            v = w[1]; src = "Luau constant"
        elif f["default"] is not None:
            v = f["default"]; src = "shader default"
        else:
            v = 0.0; src = "no value found (0)"
        if kind == "slider":
            vals = v if isinstance(v, (list, tuple)) else [v] * f["count"]
            for c in range(f["count"]):
                L.append(f"rsfx.property({idx + c}).setValue({_js(float(vals[c]))});")
        elif kind == "color":
            vals = list(v) if isinstance(v, (list, tuple)) else [v, v, v, 1]
            L.append(f"rsfx.property({idx}).setValue({_js([float(x) for x in (vals + [1, 1, 1, 1])[:4]])});")
        elif kind == "point":
            vals = list(v) if isinstance(v, (list, tuple)) else [v, v]
            L.append(f"rsfx.property({idx}).setValue({_js([float(vals[0]), float(vals[1])])});")
        notes.append(f"{name} = {v} ({src})")
    if step_fps:
        L.append(f"rsfx.property({IDX_STEP_FPS}).setValue({_js(float(step_fps))});")
        notes.append(f"Step fps {step_fps}")
    if fx_fps:
        L.append(f"rsfx.property({IDX_FX_FPS}).setValue({_js(float(fx_fps))});")
        notes.append(f"FX fps {fx_fps}")
    for tname in textures:
        if tname in (passes or {}):
            continue
        notes.append(f"texture {tname} -> Layer param left empty (transparent): the Rive script renders it from its own pass (declare it in ae_passes.json)")
    return L, notes, step_fps


def passes_for(project_dir, element_name):
    """ae_passes.json next to scene.rml -> {"source": {vm overrides}, "<texture>": {...}} for this element (or {})."""
    import json
    p = os.path.join(project_dir, "ae_passes.json")
    if not os.path.exists(p):
        return {}
    try:
        data = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    d = data.get(element_name) or {}
    return {k: v for k, v in d.items() if isinstance(v, dict)}


def _js(v):
    import json
    return json.dumps(v)
