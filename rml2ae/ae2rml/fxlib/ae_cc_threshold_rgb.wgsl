// After Effects "CC Threshold RGB" (CC Threshold RGB) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Each channel against its own threshold (raw 0..1 in AE, compared on 0..255 levels) gives 0 or 1; Invert Red/Green/Blue swap per channel; Blend w.
// Original mixes with the source. Alpha untouched; premultiplied.
struct Params {
    size: vec2<f32>,
    rT: f32,              // AE 1 Red Threshold
    gT: f32,              // AE 2 Green Threshold
    bT: f32,              // AE 3 Blue Threshold
    rI: f32,              // AE 4 Invert Red
    gI: f32,              // AE 5 Invert Green
    bI: f32,              // AE 6 Invert Blue
    blend: f32,           // AE 7 Blend w. Original (raw 0..1)
    passIndex: f32,
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let v = round(c * 255.0);
    var o = select(vec3<f32>(0.0), vec3<f32>(1.0), v >= round(vec3<f32>(P.rT, P.gT, P.bT) * 255.0));
    let inv = vec3<f32>(P.rI, P.gI, P.bI) > vec3<f32>(0.5);
    o = select(o, vec3<f32>(1.0) - o, inv);
    return out8(mix(o, c, clamp(P.blend, 0.0, 1.0)), s.a);
}
