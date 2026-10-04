// After Effects "CC Color Offset" (CC Color Offset) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Each channel is shifted by its Phase (degrees / 360 of the 0..1 range); Overflow 1 Wrap keeps fract(v), 2 Solarize
// folds it back (triangle), 3 Polarize clamps. Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    rPhase: f32,          // AE 1 Red Phase (degrees)
    gPhase: f32,          // AE 2 Green Phase
    bPhase: f32,          // AE 3 Blue Phase
    overflow: f32,        // AE 4 Overflow: 1 Wrap, 2 Solarize, 3 Polarize
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let v = straight8(s) + vec3<f32>(P.rPhase, P.gPhase, P.bPhase) / 360.0;
    let mode = i32(round(P.overflow));
    var o: vec3<f32>;
    if (mode == 2) {
        let f = v - 2.0 * floor(v * 0.5);                 // 0..2
        o = vec3<f32>(1.0) - abs(f - vec3<f32>(1.0));
    } else if (mode == 3) {
        o = clamp(v, vec3<f32>(0.0), vec3<f32>(1.0));
    } else {
        o = fract(v);
    }
    return out8(o, s.a);
}
