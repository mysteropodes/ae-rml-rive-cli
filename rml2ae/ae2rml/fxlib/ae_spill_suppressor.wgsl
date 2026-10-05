// After Effects "Spill Suppressor" (ADBE Spill Suppressor) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The channel the Color To Suppress is strongest in (green for a green screen) is limited to the mean of the two
// others: g' = min(g, (r + b) / 2), mixed by Suppression %. Color Accuracy is ignored. Alpha untouched.
struct Params {
    size: vec2<f32>,
    amount: f32,          // AE 2 Suppression (%, 0..200)
    color: vec4<f32>,     // AE 1 Color To Suppress
    passIndex: f32,
    pad0: f32,
    pad1: f32,
    pad2: f32,
    pad3: f32,
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
    let k = P.color.rgb;
    var o = c;
    if (k.g >= k.r && k.g >= k.b) {
        o.g = min(c.g, 0.5 * (c.r + c.b));
    } else if (k.b >= k.r) {
        o.b = min(c.b, 0.5 * (c.r + c.g));
    } else {
        o.r = min(c.r, 0.5 * (c.g + c.b));
    }
    return out8(mix(c, o, clamp(P.amount / 100.0, 0.0, 1.0)), s.a);
}
