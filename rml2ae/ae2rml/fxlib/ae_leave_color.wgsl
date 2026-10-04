// After Effects "Leave Color" (ADBE Leave Color) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Pixels close to Color To Leave keep their colour, the others move towards their Rec.601 grey by Amount to Decolor.
// Closeness: Match Colors 1 (Using RGB) = RGB distance / sqrt(3); 2 (Using Hue) = hue distance (0..0.5) * 2.
// Kept fully below Tolerance, then a linear ramp over Edge Softness. Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    amount: f32,          // AE 1 Amount to Decolor (%)
    tolerance: f32,       // AE 3 Tolerance (%)
    color: vec4<f32>,     // AE 2 Color To Leave
    softness: f32,        // AE 4 Edge Softness (%)
    matchMode: f32,       // AE 5 Match Colors: 1 Using RGB, 2 Using Hue
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

fn hueOf(c: vec3<f32>) -> f32 {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let d = mx - mn;
    if (d <= 0.0) {
        return 0.0;
    }
    var h: f32;
    if (mx == c.r) {
        h = (c.g - c.b) / d;
    } else if (mx == c.g) {
        h = 2.0 + (c.b - c.r) / d;
    } else {
        h = 4.0 + (c.r - c.g) / d;
    }
    return fract(h / 6.0 + 1.0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    var dist: f32;
    if (i32(round(P.matchMode)) == 2) {
        let dh = abs(hueOf(c) - hueOf(P.color.rgb));
        dist = min(dh, 1.0 - dh) * 2.0;
    } else {
        dist = length(c - P.color.rgb) / sqrt(3.0);
    }
    let tol = P.tolerance / 100.0;
    let soft = max(P.softness / 100.0, 1e-4);
    let keep = 1.0 - clamp((dist - tol) / soft, 0.0, 1.0);
    let grey = vec3<f32>(dot(c, vec3<f32>(0.299, 0.587, 0.114)));
    let d = clamp(P.amount / 100.0, 0.0, 1.0) * (1.0 - keep);
    return out8(mix(c, grey, d), s.a);
}
