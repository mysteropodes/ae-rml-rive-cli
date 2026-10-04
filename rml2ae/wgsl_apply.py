"""Apply a Rive post-process shader (.wgsl) to a PNG with wgpu — phase 3 prototype (a).

Validates, outside After Effects, what the future AE plugin will do: WGSL → Metal through naga, the Rive binding
convention (group 0: 0 = source texture, 1 = sampler, 2 = `struct Params` uniforms, 3+ = extra textures), and the
uniform layout built from the struct declaration (std140-like WGSL rules: f32 4/4, vec2 8/8, vec3 12/16, vec4 16/16).

  python -m rml2ae.wgsl_apply post.wgsl in.png out.png --set grain=0.06 --set vignette=0.12 --set tick=3 \
         --tex textTex=text.png [--size 1920x1080]

`size` (vec2) defaults to the source image size; unknown fields default to 0.
"""
import os
import re
import struct
import sys

WGSL_TYPES = {"f32": ("f", 4, 4), "i32": ("i", 4, 4), "u32": ("I", 4, 4),
              "vec2<f32>": ("ff", 8, 8), "vec3<f32>": ("fff", 12, 16), "vec4<f32>": ("ffff", 16, 16),
              "vec2<i32>": ("ii", 8, 8), "vec2<u32>": ("II", 8, 8)}


def parse_shader(src):
    """-> dict(params=[(name, wgsl type)], bindings=[(group, binding, kind, name)], vs, fs)"""
    m = re.search(r"struct\s+Params\s*\{(.*?)\}", src, re.S)
    params = []
    if m:
        for line in m.group(1).split("\n"):
            line = line.split("//")[0].strip().rstrip(",;")
            mm = re.match(r"(\w+)\s*:\s*([\w<>]+)", line)
            if mm:
                params.append((mm.group(1), mm.group(2)))
    bindings = []
    for mm in re.finditer(r"@group\((\d+)\)\s*@binding\((\d+)\)\s*var(<uniform>|<storage[^>]*>)?\s+(\w+)\s*:\s*([\w<>]+)", src):
        g, b, space, name, typ = mm.groups()
        kind = "uniform" if space == "<uniform>" else ("sampler" if typ == "sampler" else ("texture" if typ.startswith("texture_") else "other"))
        bindings.append((int(g), int(b), kind, name))
    vs = re.search(r"@vertex\s*fn\s+(\w+)", src)
    fs = re.search(r"@fragment\s*fn\s+(\w+)", src)
    return dict(params=params, bindings=bindings, vs=vs.group(1) if vs else "vs_main", fs=fs.group(1) if fs else "fs_main")


def pack_params(params, values):
    """struct Params layout -> bytes (WGSL uniform alignment rules)."""
    out = bytearray()
    for name, typ in params:
        fmt, size, align = WGSL_TYPES.get(typ, ("f", 4, 4))
        while len(out) % align:
            out += b"\0"
        v = values.get(name, 0)
        if isinstance(v, (int, float)):
            v = [v] * len(fmt)
        v = list(v)[:len(fmt)] + [0] * (len(fmt) - len(v))
        out += struct.pack("<" + fmt, *[(int(x) if c in "iI" else float(x)) for x, c in zip(v, fmt)])
    while len(out) % 16:
        out += b"\0"
    return bytes(out)


def apply(shader_path, src_png, out_png, values=None, textures=None, size=None):
    import wgpu
    import numpy as np
    from PIL import Image
    values = dict(values or {})
    textures = dict(textures or {})
    src = open(shader_path, encoding="utf-8").read()
    info = parse_shader(src)
    img = Image.open(src_png).convert("RGBA")
    w, h = size or img.size
    values.setdefault("size", (w, h))
    adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
    if adapter is None:      # no GPU (e.g. a CI machine): the software adapter (WARP on Windows, lavapipe on Linux)
        adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance", force_fallback_adapter=True)
    device = adapter.request_device_sync()
    shader = device.create_shader_module(code=src)          # naga: WGSL -> MSL on macOS
    fmt = wgpu.TextureFormat.rgba8unorm

    def upload(pil):
        pil = pil.convert("RGBA")
        data = np.asarray(pil).astype(np.uint8)
        tex = device.create_texture(size=(pil.width, pil.height, 1), format=fmt,
                                    usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST)
        device.queue.write_texture({"texture": tex, "mip_level": 0, "origin": (0, 0, 0)}, data.tobytes(),
                                   {"offset": 0, "bytes_per_row": pil.width * 4, "rows_per_image": pil.height}, (pil.width, pil.height, 1))
        return tex
    sampler = device.create_sampler(mag_filter="linear", min_filter="linear", address_mode_u="clamp-to-edge", address_mode_v="clamp-to-edge")
    ubo_bytes = pack_params(info["params"], values)
    ubo = device.create_buffer_with_data(data=ubo_bytes, usage=wgpu.BufferUsage.UNIFORM)
    layout_entries, group_entries = [], []
    first_tex = True
    for g, b, kind, name in info["bindings"]:
        if g != 0:
            raise SystemExit(f"only @group(0) is supported by the convention (found group {g} for {name})")
        if kind == "texture":
            if first_tex:
                tex, first_tex = upload(img), False
            elif name in textures:
                tex = upload(Image.open(textures[name]))
            else:
                tex = upload(Image.new("RGBA", (w, h), (0, 0, 0, 0)))
            layout_entries.append({"binding": b, "visibility": wgpu.ShaderStage.FRAGMENT, "texture": {"sample_type": "float", "view_dimension": "2d"}})
            group_entries.append({"binding": b, "resource": tex.create_view()})
        elif kind == "sampler":
            layout_entries.append({"binding": b, "visibility": wgpu.ShaderStage.FRAGMENT, "sampler": {"type": "filtering"}})
            group_entries.append({"binding": b, "resource": sampler})
        elif kind == "uniform":
            layout_entries.append({"binding": b, "visibility": wgpu.ShaderStage.FRAGMENT | wgpu.ShaderStage.VERTEX, "buffer": {"type": "uniform"}})
            group_entries.append({"binding": b, "resource": {"buffer": ubo, "offset": 0, "size": len(ubo_bytes)}})
    bgl = device.create_bind_group_layout(entries=layout_entries)
    bg = device.create_bind_group(layout=bgl, entries=group_entries)
    pipeline = device.create_render_pipeline(
        layout=device.create_pipeline_layout(bind_group_layouts=[bgl]),
        vertex={"module": shader, "entry_point": info["vs"], "buffers": []},
        primitive={"topology": wgpu.PrimitiveTopology.triangle_list, "cull_mode": "none"},
        fragment={"module": shader, "entry_point": info["fs"], "targets": [{"format": fmt}]})
    target = device.create_texture(size=(w, h, 1), format=fmt, usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC)
    enc = device.create_command_encoder()
    rp = enc.begin_render_pass(color_attachments=[{"view": target.create_view(), "load_op": "clear", "store_op": "store", "clear_value": (0, 0, 0, 0)}])
    rp.set_pipeline(pipeline)
    rp.set_bind_group(0, bg)
    rp.draw(3)
    rp.end()
    bpr = (w * 4 + 255) // 256 * 256
    readback = device.create_buffer(size=bpr * h, usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ)
    enc.copy_texture_to_buffer({"texture": target}, {"buffer": readback, "offset": 0, "bytes_per_row": bpr, "rows_per_image": h}, (w, h, 1))
    device.queue.submit([enc.finish()])
    readback.map_sync(wgpu.MapMode.READ)
    data = np.frombuffer(readback.read_mapped(), dtype=np.uint8).reshape(h, bpr)[:, : w * 4].reshape(h, w, 4)
    Image.fromarray(data.copy(), "RGBA").save(out_png)
    readback.unmap()
    return info


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    values, textures, size = {}, {}, None
    i = 0
    while i < len(argv):
        if argv[i] == "--set":
            k, v = argv[i + 1].split("=", 1)
            values[k] = [float(x) for x in v.split(",")] if "," in v else float(v)
            args = [a for a in args if a != argv[i + 1]]
            i += 2
        elif argv[i] == "--tex":
            k, v = argv[i + 1].split("=", 1)
            textures[k] = v
            args = [a for a in args if a != argv[i + 1]]
            i += 2
        elif argv[i] == "--size":
            size = tuple(int(x) for x in argv[i + 1].split("x"))
            args = [a for a in args if a != argv[i + 1]]
            i += 2
        else:
            i += 1
    if len(args) < 3:
        print(__doc__)
        return 1
    info = apply(args[0], args[1], args[2], values, textures, size)
    print("params:", ", ".join(f"{n}:{t}" for n, t in info["params"]))
    print("bindings:", ", ".join(f"{b}={k} {n}" for g, b, k, n in info["bindings"]))
    print("wrote", args[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
