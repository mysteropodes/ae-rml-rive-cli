"""AE-compatible WGSL effects library (ae2rml ⇄ rml2ae).

Each effect = `fxlib/ae_<slug>.wgsl` + `fxlib/<slug>.json` (manifest). Contract:
  * WGSL: Rive post-process convention — @group(0): 0 `srcTex` (layer content, PREMULTIPLIED RGBA), 1 sampler,
    2 `var<uniform> P: Params`, 3+ extra textures named `origTex` (the original content, for multi-pass effects) /
    `mapTex` (a second layer, e.g. Displacement Map). Full-screen triangle vertex stage (copy `vs_main` of an existing
    effect). Output premultiplied RGBA. Pixels outside the content are transparent (no clamp-to-edge smear unless the
    AE effect repeats edge pixels).
  * `struct Params` starts with `size: vec2<f32>` (canvas px) and ends with `passIndex: f32` (`pass` is a WGSL keyword) (+ pads);
    an optional reserved `layerRect: vec4<f32>` (after passIndex) = the layer's rect in the canvas (x0, y0, x1, y1): the
    canvas is the layer grown by fxPad on every side when an effect of the stack spills out; the fields between
    are the AE parameters IN AE UNITS (raw values: degrees, px, 0..255 thresholds, 0..100 percents, 1-based menu
    indices, colours as straight vec4 0..1, points as layer px). All conversion maths lives in the shader, so the
    manifest is a plain index map and rml2ae can map the fields straight back to the native AE effect.
  * Manifest: {"slug", "aeMatchName", "wgsl", "passes": N, "textures": {"origTex": "original", "mapTex": "map"},
    "params": [{"ae": <1-based AE param index>, "field": <Params field>, "kind": number|point|color|enum|bool|angle,
    "default": <AE default>}], "notes", "measured": {...}}
  * Multi-pass: the same shader runs N times; pass i reads the previous pass output as srcTex (pass 0 reads the
    content) and `P.passIndex = i`; `origTex` always holds the untouched content.

CLI:
  python -m rml2ae.ae2rml.fxlib check <slug> [--rive] [--holdout] [--keep]   AE refs vs offline wgpu (and Rive CLI)
  python -m rml2ae.ae2rml.fxlib luau <slug>[,<slug>…]                           print the generated Rive node (stack)
  python -m rml2ae.ae2rml.fxlib regress [<slug>…] [--update] [--summary FILE]  every reference (held-out included)
      vs the recorded baseline fxref/baseline.json; fails when an effect gets further from After Effects (CI)
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "fxlib")
REF = os.environ.get("AE2RML_FXREF") or os.path.join(HERE, "fxref")      # reference renders + spec (fxref/spec.py)
CHECK_OUT = os.path.expanduser(os.environ.get("AE2RML_FXCHECK_OUT", "~/.cache/ae2rml/fxcheck"))   # check outputs
TYPES = {"f32": (1, 4, 4), "i32": (1, 4, 4), "u32": (1, 4, 4), "vec2<f32>": (2, 8, 8), "vec3<f32>": (3, 12, 16),
         "vec4<f32>": (4, 16, 16), "vec2f": (2, 8, 8), "vec3f": (3, 12, 16), "vec4f": (4, 16, 16)}


MIX_WGSL = os.path.join(LIB, "_ae_fx_mix.wgsl")


def manifest(slug):
    return json.load(open(os.path.join(LIB, slug + ".json"), encoding="utf-8"))


def all_slugs():
    return sorted(f[:-5] for f in os.listdir(LIB) if f.endswith(".json") and not f.startswith("_")) if os.path.isdir(LIB) else []


def by_match_name():
    out = {}
    for s in all_slugs():
        try:
            m = manifest(s)
        except Exception:
            continue                          # a manifest being written
        if m.get("auto") is False:
            continue                          # measured worse than leaving the effect out (stays a todo comment)
        if m.get("aeMatchName") and m.get("wgsl") and os.path.exists(wgsl_path(m)):
            out.setdefault(m["aeMatchName"], m)
    return out


_AE_PARAMS = None


def ae_params(match_name):
    """AE's parameter list of an effect, measured in AE 2026 (fxlib/_ae_params.json): [{i, mn, name, type, default…}].
    The position i (ExtendScript property(i), the manifests' "ae") is NOT always the matchName's number: ADBE Fill's
    Color is property(3) but 'ADBE Fill-0002'"""
    global _AE_PARAMS
    if _AE_PARAMS is None:
        _AE_PARAMS = json.load(open(os.path.join(LIB, "_ae_params.json"), encoding="utf-8"))
    return (_AE_PARAMS.get(match_name) or {}).get("params", [])


def param_match_name(match_name, index):
    for p in ae_params(match_name):
        if int(p["i"]) == int(index):
            return p["mn"]
    return f"{match_name}-{int(index):04d}"


def wgsl_path(m):
    return os.path.join(LIB, m["wgsl"])


def struct_fields(src):
    """struct Params -> [(name, type, count, offset)] with WGSL uniform alignment; total size (16-aligned)"""
    m = re.search(r"struct\s+Params\s*\{(.*?)\}", src, re.S)
    out, off = [], 0
    for line in (m.group(1).split("\n") if m else []):
        line = line.split("//")[0].strip().rstrip(",;").strip()
        fm = re.match(r"(\w+)\s*:\s*([\w<>]+)", line)
        if not fm:
            continue
        count, size, align = TYPES.get(fm.group(2), (1, 4, 4))
        while off % align:
            off += 1
        out.append((fm.group(1), fm.group(2), count, off))
        off += size
    return out, (off + 15) // 16 * 16


def values_for(m, ae_vals, size):
    """AE param values {index: value} (raw) -> {field: value} for wgsl_apply"""
    vals = {"size": list(size), "layerRect": [0.0, 0.0, float(size[0]), float(size[1])]}   # offline: no pad
    by_idx = {str(k): v for k, v in (ae_vals or {}).items()}
    for p in m["params"]:
        v = by_idx.get(str(p["ae"]), p.get("default", 0))
        if isinstance(v, list) and p["kind"] in ("number", "enum", "bool", "angle"):
            v = v[0]
        vals[p["field"]] = v
    return vals


def run_offline(slug, ae_vals, src_png, out_png, map_png=None):
    """offline wgpu render (rml2ae.wgsl_apply), passes chained"""
    from rml2ae.wgsl_apply import apply
    from PIL import Image
    m = manifest(slug)
    size = Image.open(src_png).size
    vals = values_for(m, ae_vals, size)
    tex_roles = m.get("textures", {})
    cur = src_png
    tmpd = tempfile.mkdtemp(prefix="fxlib_")
    try:
        for i in range(int(m.get("passes", 1))):
            vals["passIndex"] = float(i)
            textures = {}
            for name, role in tex_roles.items():
                if role == "original":
                    textures[name] = src_png
                elif role == "map" and map_png:
                    textures[name] = map_png
            dst = out_png if i == int(m.get("passes", 1)) - 1 else os.path.join(tmpd, f"p{i}.png")
            apply(wgsl_path(m), cur, dst, values=vals, textures=textures)
            cur = dst
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    return out_png


# ------------------------------------------------------------------ Rive node (generated per effect STACK)
RESERVED = ("fxSource", "fxAnimation", "fxTime", "fxMix", "fxPad")


def stack_name(slugs, groups=None):
    """script name of the node running these effects in order (rml2ae splits it back on '__'); several groups (one per
    AE layer) add '__g<size>_<size>…'"""
    name = "fx_" + "__".join(slugs)
    if groups and len(groups) > 1:
        name += "__g" + "_".join(str(len(g)) for g in groups)
    return name


def parse_stack_name(name):
    """'fx_tint__invert__g1_1' -> (['tint', 'invert'], [[1], [2]])"""
    parts = name[3:].split("__") if name.startswith("fx_") else []
    groups = None
    if parts and re.fullmatch(r"g\d+(_\d+)*", parts[-1]):
        sizes = [int(x) for x in parts.pop()[1:].split("_")]
        groups, k = [], 1
        for n in sizes:
            groups.append(list(range(k, k + n)))
            k += n
    return parts, groups


def _tex_bindings(src):
    return [(int(b), name) for b, name in re.findall(r"@group\(0\)\s*@binding\((\d+)\)\s*var\s+(\w+)\s*:\s*texture_2d", src)]


MIX_BLEND = {"NORMAL": 0, "MULTIPLY": 1, "SCREEN": 2, "OVERLAY": 3, "DARKEN": 4, "LIGHTEN": 5, "COLOR_DODGE": 6,
             "CLASSIC_COLOR_DODGE": 6, "COLOR_BURN": 7, "CLASSIC_COLOR_BURN": 7, "HARD_LIGHT": 8, "SOFT_LIGHT": 9,
             "DIFFERENCE": 10, "CLASSIC_DIFFERENCE": 10, "EXCLUSION": 11, "ADD": 12, "LINEAR_DODGE": 12,
             "LINEAR_BURN": 13, "SUBTRACT": 14, "HUE": 15, "SATURATION": 16, "COLOR": 17, "LUMINOSITY": 18,
             "LINEAR_LIGHT": 19, "VIVID_LIGHT": 20, "PIN_LIGHT": 21, "HARD_MIX": 22, "LIGHTER_COLOR": 23,
             "DARKER_COLOR": 24, "DIVIDE": 25}


def _schedule(effects, groups):
    """[step], output buffer. Buffer 0 = the content canvas, 1.. = GPU canvases allocated as needed so that a pass
    never writes what it reads, an effect's input stays readable for all its passes (origTex) and a group's input stays
    readable until its mix pass. step = ("fx", effect, pass, read, dst, effect input) | ("mix", group, read, dst, group
    input)"""
    steps, inp = [], 0
    by_i = {e["i"]: e for e in effects}

    def free(*live):
        b = 1
        while b in live:
            b += 1
        return b
    for gi, grp in enumerate(groups, 1):
        g_in = inp
        for ei in grp:
            e = by_i[ei]
            e_in = prev = inp
            for p in range(e["passes"]):
                dst = free(g_in, e_in, prev)
                steps.append(("fx", ei, p, prev, dst, e_in))
                prev = dst
            inp = prev
        dst = free(g_in, inp)
        steps.append(("mix", gi, inp, dst, g_in))
        inp = dst
    return steps, inp


def luau_for(slugs, groups=None):
    """the Rive node of an effect stack: renders `fxSource` (at `fxTime` of `fxAnimation`) into a canvas, then runs on
    the GPU, group after group (a group = one AE layer: its effects in AE order, then its mix + blend mode with what it
    was applied to), and draws the result. One node per stack because an effect node rendered inside another one's
    canvas draws nothing in Rive (measured). groups: [[effect index…]…] (1-based, first group applied first); default =
    one group of every effect, driven by fxMix / fxBlend; several groups: g<j>Mix / g<j>Blend"""
    if isinstance(slugs, str):
        slugs = [slugs]
    groups = groups or [list(range(1, len(slugs) + 1))]
    single = len(groups) == 1
    effects = []
    for i, slug in enumerate(slugs, 1):
        m = manifest(slug)
        src = open(wgsl_path(m), encoding="utf-8").read()
        fields, ubo_size = struct_fields(src)
        effects.append(dict(i=i, slug=slug, m=m, fields=fields, ubo=ubo_size, tex=m.get("textures", {}),
                            binds=_tex_bindings(src), passes=max(1, int(m.get("passes", 1))),
                            shader=os.path.splitext(m["wgsl"])[0]))
    steps, out_buf = _schedule(effects, groups)
    ngpu = max([st[4] if st[0] == "fx" else st[3] for st in steps] + [1])
    mix_names = [("fxMix", "fxBlend") if single else (f"g{j}Mix", f"g{j}Blend") for j in range(1, len(groups) + 1)]
    inputs, defaults, uni_funcs = [], [], []
    for mn_, bn_ in mix_names:
        inputs.append(f"    {mn_}: Input<number>,\n    {bn_}: Input<number>,")
        defaults.append(f"        {mn_} = 1, {bn_} = 0,")
    for e in effects:
        pre = f"e{e['i']}_"
        kinds = {p["field"]: p for p in e["m"]["params"]}
        writes = []
        for name, typ, count, off in e["fields"]:
            p = kinds.get(name)
            if name == "size":
                writes.append(f"    buffer.writef32(b, {off}, self.w)\n    buffer.writef32(b, {off + 4}, self.h)")
                continue
            if name == "passIndex":
                writes.append(f"    buffer.writef32(b, {off}, pass)")
                continue
            if name == "layerRect":
                # reserved: the layer's own rect in the canvas (x0, y0, x1, y1) — the canvas is grown by fxPad
                writes.append(f"    do\n        local li = self.inst :: Artboard<nil>\n"
                              f"        buffer.writef32(b, {off}, self.fxPad)\n        buffer.writef32(b, {off + 4}, self.fxPad)\n"
                              f"        buffer.writef32(b, {off + 8}, self.fxPad + li.width)\n        buffer.writef32(b, {off + 12}, self.fxPad + li.height)\n    end")
                continue
            if p is None:
                continue                                   # pads: 0
            k, d, f = p["kind"], p.get("default", 0), pre + name
            if k == "color":
                dv = d if isinstance(d, list) else [1, 1, 1, 1]
                inputs.append(f"    {f}: Input<Color>,")
                defaults.append(f"        {f} = Color.rgba({round(dv[0] * 255)}, {round(dv[1] * 255)}, {round(dv[2] * 255)}, {round((dv[3] if len(dv) > 3 else 1) * 255)}),")
                writes.append(f"    do\n        local c = self.{f}\n"
                              f"        buffer.writef32(b, {off}, (Color.red(c) :: number) / 255)\n"
                              f"        buffer.writef32(b, {off + 4}, (Color.green(c) :: number) / 255)\n"
                              f"        buffer.writef32(b, {off + 8}, (Color.blue(c) :: number) / 255)\n"
                              f"        buffer.writef32(b, {off + 12}, (Color.alpha(c) :: number) / 255)\n    end")
            elif k == "point":
                dv = d if isinstance(d, list) else [0, 0]
                inputs.append(f"    {f}X: Input<number>,\n    {f}Y: Input<number>,")
                defaults.append(f"        {f}X = {dv[0]}, {f}Y = {dv[1]},")
                writes.append(f"    buffer.writef32(b, {off}, self.{f}X + self.fxPad)\n    buffer.writef32(b, {off + 4}, self.{f}Y + self.fxPad)")
            else:
                dv = d[0] if isinstance(d, list) else d
                inputs.append(f"    {f}: Input<number>,")
                defaults.append(f"        {f} = {dv},")
                writes.append(f"    buffer.writef32(b, {off}, self.{f})")
        uni_funcs.append(f"local function uni{e['i']}(self: Fx, pass: number): buffer\n    local b = buffer.create({e['ubo']})\n"
                         + "\n".join(writes) + "\n    return b\nend\n")
    maps = [e for e in effects if "map" in e["tex"].values()]
    map_fields = "".join(f"    e{e['i']}_mapSource: Input<Artboard<nil>>,\n    e{e['i']}_mapInst: Artboard<nil>?,\n    e{e['i']}_mapCanvas: Canvas?,\n" for e in maps)
    map_defaults = "".join(f"        e{e['i']}_mapSource = late(), e{e['i']}_mapInst = nil, e{e['i']}_mapCanvas = nil,\n" for e in maps)
    map_init = "".join(f"    do\n        local mi = self.e{e['i']}_mapSource:instance()\n        mi.frameOrigin = false\n        mi:advance(0)\n"
                       f"        self.e{e['i']}_mapInst = mi\n        self.e{e['i']}_mapCanvas = context:canvas({{ width = w, height = h, clearColor = Color.rgba(0, 0, 0, 0) }})\n    end\n" for e in maps)
    map_adv = "".join(f"    if self.e{e['i']}_mapInst then (self.e{e['i']}_mapInst :: Artboard<nil>):advance(dt) end\n" for e in maps)
    map_prime = "".join(f"    if self.e{e['i']}_mapInst then (self.e{e['i']}_mapInst :: Artboard<nil>):draw(renderer) end\n" for e in maps)
    map_draw = "".join(f"    if self.e{e['i']}_mapInst and self.e{e['i']}_mapCanvas then render(self.e{e['i']}_mapCanvas :: Canvas, self.e{e['i']}_mapInst :: Artboard<nil>, self.fxPad) end\n" for e in maps)
    pipes = []
    for e in effects:
        pipes.append(f"    local sh{e['i']} = context:shader({json.dumps(e['shader'])})\n    if not sh{e['i']} then\n        print(\"fx: shader {e['shader']} missing\")\n        return false\n    end\n"
                     f"    self.pipes[{e['i']}] = GPUPipeline.new({{ vertex = sh{e['i']}, fragment = sh{e['i']}, vertexLayout = {{}}, colorTargets = {{ {{ format = fmt }} }}, topology = \"triangle-list\", cullMode = \"none\" }})")
    groups_code, runs = [], []
    by_i = {e["i"]: e for e in effects}

    def view(b):
        return "cv" if b == 0 else f"self.gpus[{b}].image:view()"
    for s_idx, st in enumerate(steps, 1):
        if st[0] == "fx":
            _k, ei, p, rd, dst, orig = st
            e = by_i[ei]
            tex = [f"{{ slot = 0, view = {view(rd)} }}"]
            for b, name in e["binds"]:
                if b == 0:
                    continue
                role = e["tex"].get(name)
                v = view(orig) if role == "original" else (f"(self.e{ei}_mapCanvas :: Canvas).image:view()" if role == "map" else view(orig))
                tex.append(f"{{ slot = {b}, view = {v} }}")
            groups_code.append(f"    self.ubos[{s_idx}] = GPUBuffer.new({{ size = {e['ubo']}, usage = \"uniform\" }})\n"
                               f"    self.groups[{s_idx}] = GPUBindGroup.new({{\n        layout = self.pipes[{ei}]:getBindGroupLayout(0),\n"
                               f"        textures = {{ {', '.join(tex)} }},\n        samplers = {{ {{ slot = 1, sampler = sampler }} }},\n"
                               f"        ubos = {{ {{ slot = 2, buffer = self.ubos[{s_idx}] }} }},\n    }})")
            runs.append(f"    do -- effect {ei} ({e['slug']}) pass {p}\n        self.ubos[{s_idx}]:write(uni{ei}(self, {p}))\n"
                        f"        local rp = self.gpus[{dst}]:beginRenderPass({{ color = {{ {{ loadOp = \"clear\", storeOp = \"store\", clearColor = {{ 0, 0, 0, 0 }} }} }} }})\n"
                        f"        rp:setPipeline(self.pipes[{ei}])\n        rp:setBindGroup(0, self.groups[{s_idx}])\n        rp:draw(3)\n        rp:finish()\n    end")
        else:
            _k, gi, rd, dst, g_in = st
            mn_, bn_ = mix_names[gi - 1]
            groups_code.append(f"    self.ubos[{s_idx}] = GPUBuffer.new({{ size = 32, usage = \"uniform\" }})\n"
                               f"    self.groups[{s_idx}] = GPUBindGroup.new({{\n        layout = mixPipe:getBindGroupLayout(0),\n"
                               f"        textures = {{ {{ slot = 0, view = {view(rd)} }}, {{ slot = 3, view = {view(g_in)} }} }},\n"
                               f"        samplers = {{ {{ slot = 1, sampler = sampler }} }},\n        ubos = {{ {{ slot = 2, buffer = self.ubos[{s_idx}] }} }},\n    }})")
            runs.append(f"    do -- group {gi}: mix {mn_} / blend {bn_} with what it was applied to\n        local mb = buffer.create(32)\n"
                        f"        buffer.writef32(mb, 0, self.w)\n        buffer.writef32(mb, 4, self.h)\n"
                        f"        buffer.writef32(mb, 8, math.clamp(self.{mn_}, 0, 1))\n        buffer.writef32(mb, 12, self.{bn_})\n"
                        f"        self.ubos[{s_idx}]:write(mb)\n"
                        f"        local rp = self.gpus[{dst}]:beginRenderPass({{ color = {{ {{ loadOp = \"clear\", storeOp = \"store\", clearColor = {{ 0, 0, 0, 0 }} }} }} }})\n"
                        f"        rp:setPipeline(self.mixPipe :: GPUPipeline)\n        rp:setBindGroup(0, self.groups[{s_idx}])\n        rp:draw(3)\n        rp:finish()\n    end")
    more_gpus = (f"    for k = 2, {ngpu} do\n        self.gpus[k] = context:gpuCanvas({{ width = w, height = h }})\n    end\n"
                 if ngpu > 1 else "")
    header = "\n".join(f"--   {e['i']}. \"{e['m']['aeMatchName']}\" ({e['m']['wgsl']}, {e['passes']} pass{'es' if e['passes'] > 1 else ''})" for e in effects)
    group_doc = "; ".join(f"group {j}: effects {', '.join(map(str, g))}" for j, g in enumerate(groups, 1))
    all_off = " and ".join(f"self.{mn_} <= 0.0005" for mn_, _b in mix_names)
    return f"""--!strict
-- Generated by ae2rml (rml2ae/ae2rml/fxlib.py) — After Effects effects as WGSL post-processes, in AE's stack order:
{header}
-- {group_doc} (a group = one AE layer's effects, then its mix / blend mode with what it was applied to)
-- fxlib-stack: {",".join(slugs)}
-- fxlib-groups: {"|".join(",".join(map(str, g)) for g in groups)}
-- Inputs: the AE parameters in AE units (keyable), prefixed by their effect's rank (e1_, e2_…); fxSource = the artboard
-- holding the content, played at fxTime of fxAnimation; {"fxMix / fxBlend" if single else "g<j>Mix / g<j>Blend"} = opacity (0..1) and blend mode
-- (_ae_fx_mix.wgsl codes) of the layer the effects belong to; fxPad grows the canvas for effects that spill out.
type Fx = {{
    fxSource: Input<Artboard<nil>>,
    fxAnimation: Input<string>,
    fxTime: Input<number>,
    fxPad: Input<number>,
{chr(10).join(inputs)}
{map_fields}    inst: Artboard<nil>?,
    anim: Animation?,
    canvas: Canvas?,
    gpus: {{ GPUCanvas }},
    pipes: {{ GPUPipeline }},
    ubos: {{ GPUBuffer }},
    groups: {{ GPUBindGroup }},
    drawSampler: ImageSampler?,
    mixPipe: GPUPipeline?,
    out: GPUCanvas?,
    rendered: boolean,
    w: number,
    h: number,
}}

-- nothing is drawn through an empty clip: lets the effect nodes NESTED in the source do their GPU work before this
-- node opens its own canvas frame; inside our frame they then only draw their cached image (ae2rml also switches off
-- effect nodes nested in another one's content: a canvas does not show a GPU canvas drawn into it)
local EMPTY = Path.new()

local function render(canvas: Canvas, inst: Artboard<nil>, pad: number)
    local r = canvas:beginFrame({{ clearColor = Color.rgba(0, 0, 0, 0) }})
    r:save()
    r:transform(Mat2D.withTranslation(pad, pad))
    inst:draw(r)
    r:restore()
    canvas:endFrame()
end

{chr(10).join(uni_funcs)}
local function init(self: Fx, context: Context): boolean
    local inst = self.fxSource:instance()
    inst.frameOrigin = false
    if self.fxAnimation ~= "" then
        self.anim = inst:animation(self.fxAnimation)
    end
    if self.anim then
        (self.anim :: Animation):setTime(self.fxTime)
    end
    inst:advance(0)
    self.inst = inst
    local w = math.max(1, math.ceil(inst.width + 2 * self.fxPad))
    local h = math.max(1, math.ceil(inst.height + 2 * self.fxPad))
    self.w, self.h = w, h
    self.canvas = context:canvas({{ width = w, height = h, clearColor = Color.rgba(0, 0, 0, 0) }})
{map_init}    local first = context:gpuCanvas({{ width = w, height = h }})
    local fmt: ColorFormat = first.format :: ColorFormat
    self.gpus = {{ first }}
{more_gpus}{chr(10).join(pipes)}
    local mixShader = context:shader("_ae_fx_mix")
    if not mixShader then
        print("fx: shader _ae_fx_mix missing")
        return false
    end
    local mixPipe = GPUPipeline.new({{ vertex = mixShader, fragment = mixShader, vertexLayout = {{}}, colorTargets = {{ {{ format = fmt }} }}, topology = "triangle-list", cullMode = "none" }})
    self.mixPipe = mixPipe
    local sampler = GPUSampler.new({{ min = "linear", mag = "linear", wrapU = "clamp-to-edge", wrapV = "clamp-to-edge" }})
    self.drawSampler = ImageSampler("clamp", "clamp", "bilinear")
    local cv = (self.canvas :: Canvas).image:view()
{chr(10).join(groups_code)}
    return true
end

local function advance(self: Fx, dt: number): boolean
    self.rendered = false                  -- new frame: the GPU work runs again at the next draw
    local inst = self.inst
    if inst then
        if self.anim then
            -- impose the converted time (the source artboard has no default state machine replaying from 0). Two
            -- advance(0): the second one hands the new time to the artboards nested in the instance (promo-test: one
            -- step gained). Extrapolating fxTime one step ahead gained another there but broke Shape_01 (0.04 -> 0.8 %
            -- of pixels off AE): not kept.
            local anim = self.anim :: Animation
            anim:setTime(self.fxTime)
            inst:advance(0)
            inst:advance(0)
        else
            inst:advance(dt)
        end
    end
{map_adv}    return true
end

local function draw(self: Fx, renderer: Renderer)
    local inst, canvas, drawSampler = self.inst, self.canvas, self.drawSampler
    if not (inst and canvas and drawSampler) then
        return
    end
    if {all_off} then
        -- every effect off (adjustment layers outside their in/out window): the content as is
        renderer:save()
        inst:draw(renderer)
        renderer:restore()
        return
    end
    if not (self.rendered and self.out) then
        renderer:save()
        renderer:clipPath(EMPTY)
        inst:draw(renderer)                -- primes nested effect nodes, draws nothing
{map_prime}        renderer:restore()
        render(canvas, inst, self.fxPad)
{map_draw}{chr(10).join(runs)}
        self.out = self.gpus[{out_buf}]
        self.rendered = true
    end
    renderer:save()
    renderer:transform(Mat2D.withTranslation(-self.fxPad, -self.fxPad))
    renderer:drawImage((self.out :: GPUCanvas).image, drawSampler, "srcOver", 1)
    renderer:restore()
end

return function(): Node<Fx>
    return {{
        fxSource = late(), fxAnimation = "", fxTime = 0, fxPad = 0,
{chr(10).join(defaults)}
{map_defaults}        inst = nil, anim = nil, canvas = nil, gpus = {{}}, pipes = {{}}, ubos = {{}}, groups = {{}}, drawSampler = nil,
        mixPipe = nil, out = nil, rendered = false, w = 1, h = 1,
        init = init, advance = advance, draw = draw,
    }}
end
"""


def argb_hex(c):
    c = list(c) + [1] * (4 - len(c))
    # half up, as AE quantises colours (Python round() is half-to-even: 0.3 * 255 = 76.5 -> 76, AE 77)
    return "%02X%02X%02X%02X" % tuple(max(0, min(255, int(math.floor(float(x) * 255 + 0.5)))) for x in (c[3], c[0], c[1], c[2]))


def test_project(slugs, ae_vals_list, d, src_png, map_png=None):
    """a Rive CLI project: artboard Main = the stack node over artboard Src (the image); ae_vals_list = one
    {AE param index: value} per effect"""
    from PIL import Image
    if isinstance(slugs, str):
        slugs, ae_vals_list = [slugs], [ae_vals_list]
    os.makedirs(d, exist_ok=True)
    w, h = Image.open(src_png).size
    shutil.copy(src_png, os.path.join(d, "src.png"))
    shutil.copy(MIX_WGSL, os.path.join(d, "_ae_fx_mix.wgsl"))
    name = stack_name(slugs)
    open(os.path.join(d, name + ".luau"), "w", encoding="utf-8").write(luau_for(slugs))
    open(os.path.join(d, "rive.yaml"), "w", encoding="utf-8").write(f"name: {name}\n")
    ins, has_map = [], False
    for i, (slug, ae_vals) in enumerate(zip(slugs, ae_vals_list), 1):
        m = manifest(slug)
        shutil.copy(wgsl_path(m), os.path.join(d, m["wgsl"]))
        by_idx = {str(k): v for k, v in (ae_vals or {}).items()}
        for p in m["params"]:
            v = by_idx.get(str(p["ae"]), p.get("default", 0))
            f = f"e{i}_{p['field']}"
            if p["kind"] == "color":
                ins.append(f'<ScriptInputColor propertyValue="{argb_hex(v)}" name="{f}"/>')
            elif p["kind"] == "point":
                ins.append(f'<ScriptInputNumber propertyValue="{v[0]}" name="{f}X"/>')
                ins.append(f'<ScriptInputNumber propertyValue="{v[1]}" name="{f}Y"/>')
            else:
                ins.append(f'<ScriptInputNumber propertyValue="{v[0] if isinstance(v, list) else v}" name="{f}"/>')
        if "map" in m.get("textures", {}).values():
            has_map = True
            ins.append(f'<ScriptInputArtboard artboardId="0:20" name="e{i}_mapSource"/>')
    if has_map:
        shutil.copy(map_png, os.path.join(d, "map.png"))
    rml = f"""<Rive version="1" kind="fragment">
<Artboard x="0" y="0" width="{w}" height="{h}" defaultStateMachineId="0:3" name="Main" id="0:1">
<Fill name="bg"><SolidColor colorValue="FF000000"/></Fill>
<ScriptedDrawable x="0" y="0" scriptAssetId="0:9" name="FX" id="0:2">
<ScriptInputArtboard artboardId="0:10" name="fxSource"/>
<ScriptInputNumber propertyValue="0" name="fxPad"/>
{chr(10).join(ins)}
</ScriptedDrawable>
<LinearAnimation duration="60" loopValue="loop" name="Idle" id="0:4"/>
<StateMachine name="SM" id="0:3"><StateMachineLayer name="L" id="0:5"><AnyState x="200" y="-120"/><ExitState x="400" y="-120"/><EntryState x="0" y="0"><StateTransition stateToId="0:6"/></EntryState><AnimationState x="200" y="0" animationId="0:4" id="0:6"/></StateMachineLayer></StateMachine>
</Artboard>
<Artboard x="{w + 100}" y="0" width="{w}" height="{h}" name="Src" id="0:10">
<Image x="0" y="0" originX="0" originY="0" assetId="0:11" name="src" id="0:12"/>
</Artboard>
{f'<Artboard x="0" y="{h + 100}" width="{w}" height="{h}" name="Map" id="0:20"><Image x="0" y="0" originX="0" originY="0" assetId="0:21" name="map" id="0:22"/></Artboard>' if has_map else ""}
<ImageAsset file="src.png" name="src" id="0:11"/>
{'<ImageAsset file="map.png" name="map" id="0:21"/>' if has_map else ""}
<ScriptAsset file="{name}.luau" name="{name}" id="0:9"/>
</Rive>
"""
    open(os.path.join(d, "scene.rml"), "w", encoding="utf-8").write(rml)
    return d


def rive_shot(d, out_png):
    env = {**os.environ, "RIVE_NO_TUI": "1"}
    v = subprocess.run(["rive", d, "--verify"], capture_output=True, text=True, env=env)
    if v.returncode != 0:
        return False, (v.stdout + v.stderr)[-1500:]
    r = subprocess.run(["rive", d, f"--screenshot={out_png}", "--artboard=Main", "--advance=0.0s", "--quiet"],
                       capture_output=True, text=True, env=env)
    if not os.path.exists(out_png):
        return False, (r.stdout + r.stderr)[-1500:]
    return True, ""


def compare(a_png, b_png, rgb_only=False):
    """AE ref (premultiplied PNG) vs result -> mean abs error /255 %, % px > 8/255, max"""
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(a_png).convert("RGBA")).astype(float)
    b = np.asarray(Image.open(b_png).convert("RGBA")).astype(float)
    if a.shape != b.shape:
        return {"error": f"shape {a.shape} vs {b.shape}"}
    if rgb_only:
        a, b = a[..., :3], b[..., :3]
    d = np.abs(a - b)
    px = d.max(-1)
    return {"mean": round(float(d.mean() / 2.55), 3), "pct_px_gt8": round(float((px > 8).mean() * 100), 3), "max": int(px.max())}


def check(slug, rive=False, holdout=False, keep=False):
    m = manifest(slug)
    rs = json.load(open(os.path.join(REF, "renders.json"), encoding="utf-8"))
    rows = []
    src = os.path.join(REF, "src", "src_premult.png")
    mp = os.path.join(REF, "src", "map.png")
    outd = os.path.join(CHECK_OUT, slug)
    os.makedirs(outd, exist_ok=True)
    for r in rs:
        if r["slug"] != slug or (r["held"] and not holdout):
            continue
        ref = os.path.join(REF, r["png"])                 # ae/<slug>_<k>.png or ae_holdout/<slug>_<k>.png
        name = f"{slug}_{r['k']}"
        o = os.path.join(outd, name + ".png")
        row = {"setting": name, "held": r["held"], "vals": r["vals"]}
        if not os.path.exists(ref):                      # spec'd, not rendered in After Effects yet
            row["missing"] = True
            rows.append(row)
            continue
        try:
            run_offline(slug, r["vals"], src, o, map_png=mp)
            row["offline"] = compare(ref, o)
        except Exception as ex:
            row["offline"] = {"error": f"{type(ex).__name__}: {str(ex)[:400]}"}
        if rive:
            d = os.path.join(outd, "rive_" + name)
            shutil.rmtree(d, ignore_errors=True)
            # Rive renders the straight-alpha image; compare RGB over black (= AE premultiplied RGB)
            test_project([slug], [r["vals"]], d, os.path.join(REF, "src", "src.png"), map_png=mp)
            shot = os.path.join(outd, "rive_" + name + ".png")
            if os.path.exists(shot):
                os.remove(shot)
            ok, err = rive_shot(d, shot)
            row["rive"] = compare(ref, shot, rgb_only=True) if ok else {"error": err}
            if not keep:
                shutil.rmtree(d, ignore_errors=True)
        rows.append(row)
    return rows


# ------------------------------------------------------------------ regression gate (CI)
BASELINE = os.path.join(REF, "baseline.json")
# allowed drift before a setting counts as worse: GPU drivers differ in the last bit of float maths and filtering
TOL = {"mean": 0.02, "pct_px_gt8": 0.05, "max": 2}


def manifest_problems(slug):
    """Static checks of one manifest against its shader and the measured AE parameter lists."""
    out = []
    try:
        m = manifest(slug)
        fields, _ = struct_fields(open(wgsl_path(m), encoding="utf-8").read())
    except Exception as ex:
        return [f"{slug}: unreadable manifest or shader ({type(ex).__name__}: {ex})"]
    names = {f[0] for f in fields}
    for req in ("size", "passIndex"):
        if req not in names:
            out.append(f"{slug}: Params has no '{req}'")
    for p in m.get("params", []):
        if p.get("field") not in names:
            out.append(f"{slug}: param ae {p.get('ae')} -> field '{p.get('field')}' is not in Params")
        if p.get("kind") not in ("number", "point", "color", "enum", "bool", "angle"):
            out.append(f"{slug}: param ae {p.get('ae')} has unknown kind {p.get('kind')!r}")
    known = ae_params(m["aeMatchName"])
    if known:
        idx = {q["i"] for q in known}
        for p in m.get("params", []):
            if p.get("ae") not in idx:
                out.append(f"{slug}: param ae {p.get('ae')} is not a parameter of {m['aeMatchName']}")
    if "map" in m.get("textures", {}).values() and not isinstance((m.get("textureParams") or {}).get("mapTex"), int):
        out.append(f"{slug}: reads a second layer (mapTex) but has no textureParams.mapTex (the AE position of its layer parameter)")
    if m.get("status") not in ("exact", "close", "approx", "unverified"):
        out.append(f"{slug}: unknown status {m.get('status')!r}")
    return out


def pad_problems(slug, pad=40):
    """An effect must not depend on the node's pad: render its first setting (or its defaults) on the reference
    source as is, then on the same source grown by `pad` transparent px with layerRect / point parameters moved
    accordingly (what the Rive node does), and compare the layer area. Checked for unverified effects that read
    layerRect (the measured ones are covered by their references)."""
    import numpy as np
    from PIL import Image
    from rml2ae.wgsl_apply import apply
    m = manifest(slug)
    fields, _ = struct_fields(open(wgsl_path(m), encoding="utf-8").read())
    if m.get("status") != "unverified" or "layerRect" not in {f[0] for f in fields} or int(m.get("passes", 1)) != 1:
        return []
    src = os.path.join(REF, "src", "src_premult.png")
    vals = next((r["vals"] for r in json.load(open(os.path.join(REF, "renders.json"), encoding="utf-8")) if r["slug"] == slug), {})
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    tmpd = tempfile.mkdtemp(prefix="fxpad_")
    try:
        big = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        big.paste(im, (pad, pad))
        big.save(os.path.join(tmpd, "big.png"))
        v1 = values_for(m, vals, (w, h))
        v1["passIndex"] = 0.0
        v2 = values_for(m, vals, (w + 2 * pad, h + 2 * pad))
        v2["passIndex"] = 0.0
        v2["layerRect"] = [pad, pad, w + pad, h + pad]
        for p in m["params"]:
            if p["kind"] == "point":
                v2[p["field"]] = [v1[p["field"]][0] + pad, v1[p["field"]][1] + pad]
        a, b = os.path.join(tmpd, "a.png"), os.path.join(tmpd, "b.png")
        apply(wgsl_path(m), src, a, values=v1)
        apply(wgsl_path(m), os.path.join(tmpd, "big.png"), b, values=v2)
        x = np.asarray(Image.open(a)).astype(int)
        y = np.asarray(Image.open(b)).astype(int)[pad:pad + h, pad:pad + w]
        d = int(np.abs(x - y).max())
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    return [f"{slug}: depends on the node's pad (max {d} levels apart with a {pad} px pad)"] if d > 2 else []


def regress(slugs=None, update=False, summary=None):
    """Measure every reference (held-out included) offline and compare with fxref/baseline.json.
    Returns the number of problems (worse than the baseline, errors, missing baseline, manifest problems)."""
    base = json.load(open(BASELINE, encoding="utf-8")) if os.path.exists(BASELINE) else {}
    slugs = slugs or all_slugs()
    problems, rows_md, new = [], [], dict(base)
    for slug in slugs:
        problems += manifest_problems(slug)
        try:
            problems += pad_problems(slug)
        except Exception as ex:
            problems.append(f"{slug}: pad check failed ({type(ex).__name__}: {str(ex)[:200]})")
        new[slug] = {}
        for row in check(slug, holdout=True):
            name = row["setting"]
            if row.get("missing"):
                line = f"| {slug} | {name}{' (held out)' if row['held'] else ''} | - | - | - | no After Effects reference yet |"
                rows_md.append(line)
                print(line, flush=True)
                continue
            got = row["offline"]
            new[slug][name] = got
            ref = base.get(slug, {}).get(name)
            verdict = "ok"
            if "error" in got:
                verdict = "error"
                problems.append(f"{name}: {got['error'][:300]}")
            elif ref is None:
                verdict = "new"
                if not update:
                    problems.append(f"{name}: no baseline (run `fxlib regress {slug} --update` and commit fxref/baseline.json)")
            elif "error" not in ref:
                worse = [k for k, t in TOL.items() if got[k] > ref[k] + t]
                better = [k for k, t in TOL.items() if got[k] < ref[k] - t]
                if worse:
                    verdict = "worse: " + ", ".join(f"{k} {ref[k]} -> {got[k]}" for k in worse)
                    problems.append(f"{name}: {verdict}")
                elif better:
                    verdict = "better: " + ", ".join(f"{k} {ref[k]} -> {got[k]}" for k in better)
            held = " (held out)" if row["held"] else ""
            line = f"| {slug} | {name}{held} | {got.get('mean', '-')} | {got.get('pct_px_gt8', '-')} | {got.get('max', '-')} | {verdict} |"
            rows_md.append(line)
            print(line, flush=True)
    if update:
        json.dump(dict(sorted(new.items())), open(BASELINE, "w", encoding="utf-8"), indent=1)
        open(BASELINE, "a", encoding="utf-8").write("\n")
        problems = [p for p in problems if "no baseline" not in p]
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## fxlib: offline WGSL vs After Effects references\n\n"
                    "mean = mean error in % of 255, >8 = % of pixels off by more than 8 levels, max = levels\n\n"
                    "| effect | setting | mean | >8 | max | vs baseline |\n|---|---|---|---|---|---|\n")
            f.write("\n".join(rows_md) + "\n\n")
            if problems:
                f.write("**Problems**\n\n" + "\n".join("- " + p for p in problems) + "\n")
    for p in problems:
        print("PROBLEM " + p)
    print(f"fxlib regress: {len(slugs)} effect(s), {len(rows_md)} setting(s), {len(problems)} problem(s)")
    return len(problems)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "check":
        for row in check(a[1], rive="--rive" in a, holdout="--holdout" in a, keep="--keep" in a):
            print(json.dumps(row))
    elif a and a[0] == "regress":
        summ = a[a.index("--summary") + 1] if "--summary" in a else None
        names = [x for x in a[1:] if not x.startswith("--") and x != summ]
        sys.exit(1 if regress(names or None, update="--update" in a, summary=summ) else 0)
    elif a and a[0] == "luau":
        print(luau_for(a[1].split(",")))
    else:
        print(__doc__)
