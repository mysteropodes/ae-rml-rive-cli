// After Effects "CC Kernel" (CC Kernel) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A 3x3 convolution of the straight colour with the nine weights, divided by Divider (0 = the sum of the weights, or 1
// when they sum to 0); Absolute Value takes |result|. Transparent pixels count as black. Alpha untouched.
struct Params {
    size: vec2<f32>,
    k1: f32,              // AE 2 Line 1 col 1
    k2: f32,              // AE 3 Line 1 col 2
    k3: f32,              // AE 4 Line 1 col 3
    k4: f32,              // AE 7 Line 2 col 1
    k5: f32,              // AE 8 Line 2 col 2
    k6: f32,              // AE 9 Line 2 col 3
    k7: f32,              // AE 12 Line 3 col 1
    k8: f32,              // AE 13 Line 3 col 2
    k9: f32,              // AE 14 Line 3 col 3
    divider: f32,         // AE 16 Divider
    absolute: f32,        // AE 17 Absolute Values
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

fn px(p: vec2<i32>) -> vec3<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    return straight8(textureLoad(srcTex, clamp(p, vec2<i32>(0), dimi - vec2<i32>(1)), 0));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    var acc = P.k1 * px(ip + vec2<i32>(-1, -1)) + P.k2 * px(ip + vec2<i32>(0, -1)) + P.k3 * px(ip + vec2<i32>(1, -1))
            + P.k4 * px(ip + vec2<i32>(-1, 0)) + P.k5 * px(ip) + P.k6 * px(ip + vec2<i32>(1, 0))
            + P.k7 * px(ip + vec2<i32>(-1, 1)) + P.k8 * px(ip + vec2<i32>(0, 1)) + P.k9 * px(ip + vec2<i32>(1, 1));
    var d = P.divider;
    if (abs(d) < 1e-6) {
        d = P.k1 + P.k2 + P.k3 + P.k4 + P.k5 + P.k6 + P.k7 + P.k8 + P.k9;
        if (abs(d) < 1e-6) {
            d = 1.0;
        }
    }
    acc = acc / d;
    if (P.absolute > 0.5) {
        acc = abs(acc);
    }
    return out8(acc, s.a);
}
