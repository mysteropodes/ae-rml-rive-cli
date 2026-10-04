// After Effects "CC Cross Blur" (CC Cross Blur) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The mean of a horizontal box blur of Radius X and a vertical box blur of Radius Y (a cross-shaped kernel), on the
// premultiplied layer; Repeat Edge Pixels clamps coordinates, otherwise transparent outside. Transfer Mode is ignored
// (normal).
struct Params {
    size: vec2<f32>,
    rx: f32,              // AE 1 Radius X (px)
    ry: f32,              // AE 2 Radius Y (px)
    transfer: f32,        // AE 3 Transfer Mode (not modelled)
    repeatEdge: f32,      // AE 4 Repeat Edge Pixels
    passIndex: f32,
    pad0: f32,
};
@group(0) @binding(0) var srcTex: texture_2d<f32>;
@group(0) @binding(1) var srcSamp: sampler;
@group(0) @binding(2) var<uniform> P: Params;

struct VSOut {
    @builtin(position) pos: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vid: u32) -> VSOut {
    var positions = array<vec2<f32>, 3>(vec2<f32>(-1.0, -1.0), vec2<f32>(3.0, -1.0), vec2<f32>(-1.0, 3.0));
    var uvs = array<vec2<f32>, 3>(vec2<f32>(0.0, 1.0), vec2<f32>(2.0, 1.0), vec2<f32>(0.0, -1.0));
    var out: VSOut;
    out.pos = vec4<f32>(positions[vid], 0.0, 1.0);
    out.uv = uvs[vid];
    return out;
}

// pixel of the canvas under this fragment, clamped to the texture
fn pixelOf(uv: vec2<f32>) -> vec2<i32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    return clamp(vec2<i32>(floor(uv * vec2<f32>(dimi))), vec2<i32>(0), dimi - vec2<i32>(1));
}

// premultiplied texel -> AE's straight 8-bit colour (black where transparent)
fn straight8(s: vec4<f32>) -> vec3<f32> {
    if (s.a <= 0.0) {
        return vec3<f32>(0.0);
    }
    return clamp(round(s.rgb / s.a * 255.0) / 255.0, vec3<f32>(0.0), vec3<f32>(1.0));
}

// straight colour + alpha -> 8-bit rounded, premultiplied output
fn out8(c: vec3<f32>, a: f32) -> vec4<f32> {
    let oc = round(clamp(c, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0) / 255.0;
    let oa = round(clamp(a, 0.0, 1.0) * 255.0) / 255.0;
    return vec4<f32>(oc * oa, oa);
}

const MAXR: i32 = 200;

fn fetch(p: vec2<i32>) -> vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    if (P.repeatEdge > 0.5) {
        return textureLoad(srcTex, clamp(p, vec2<i32>(0), dimi - vec2<i32>(1)), 0);
    }
    if (any(p < vec2<i32>(0)) || any(p >= dimi)) {
        return vec4<f32>(0.0);
    }
    return textureLoad(srcTex, p, 0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let rx = clamp(i32(round(P.rx)), 0, MAXR);
    let ry = clamp(i32(round(P.ry)), 0, MAXR);
    var h = vec4<f32>(0.0);
    for (var i = -rx; i <= rx; i++) {
        h += fetch(ip + vec2<i32>(i, 0));
    }
    var v = vec4<f32>(0.0);
    for (var i = -ry; i <= ry; i++) {
        v += fetch(ip + vec2<i32>(0, i));
    }
    let o = 0.5 * (h / f32(2 * rx + 1) + v / f32(2 * ry + 1));
    return round(o * 255.0) / 255.0;
}
