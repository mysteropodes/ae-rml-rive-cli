// After Effects "Change Color" (ADBE Change Color) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Pixels close to Color To Change (Match Colors: 1 Using RGB, 2 Using Hue, 3 Using Chroma) get Hue Transform
// (degrees), Lightness and Saturation Transforms (-100..100, added to HSL L and S) applied by a mask that is 1 below
// Matching Tolerance and ramps to 0 over Matching Softness. View 2 shows that mask; Invert Color Correction Mask
// inverts it. Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    view: f32,            // AE 1 View: 1 Corrected Layer, 2 Color Correction Mask
    hue: f32,             // AE 2 Hue Transform (degrees)
    light: f32,           // AE 3 Lightness Transform
    sat: f32,             // AE 4 Saturation Transform
    color: vec4<f32>,     // AE 5 Color To Change
    tolerance: f32,       // AE 6 Matching Tolerance (%)
    softness: f32,        // AE 7 Matching Softness (%)
    matchMode: f32,       // AE 8 Match Colors: 1 RGB, 2 Hue, 3 Chroma
    invert: f32,          // AE 9 Invert Color Correction Mask
    passIndex: f32,
    pad0: f32,
    pad1: f32,
    pad2: f32,
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

fn lumHsl(c: vec3<f32>) -> f32 {
    return 0.5 * (max(c.r, max(c.g, c.b)) + min(c.r, min(c.g, c.b)));
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

fn satOf(c: vec3<f32>) -> f32 {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let l = 0.5 * (mx + mn);
    let d = mx - mn;
    if (d <= 0.0) {
        return 0.0;
    }
    if (l <= 0.5) {
        return d / (mx + mn);
    }
    return d / (2.0 - mx - mn);
}

fn hueRgb(h: f32) -> vec3<f32> {
    return clamp(abs(fract(vec3<f32>(h) + vec3<f32>(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - vec3<f32>(3.0)) - vec3<f32>(1.0),
                 vec3<f32>(0.0), vec3<f32>(1.0));
}

fn hslToRgb(h: f32, s: f32, l: f32) -> vec3<f32> {
    var q: f32;
    if (l < 0.5) {
        q = l * (1.0 + s);
    } else {
        q = l + s - l * s;
    }
    let p = 2.0 * l - q;
    return vec3<f32>(p) + (q - p) * hueRgb(h);
}

fn chroma2(c: vec3<f32>) -> vec2<f32> {
    let y = dot(c, vec3<f32>(0.299, 0.587, 0.114));
    return vec2<f32>(c.b - y, c.r - y);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let k = P.color.rgb;
    let mode = i32(round(P.matchMode));
    var dist: f32;
    if (mode == 2) {
        let dh = abs(hueOf(c) - hueOf(k));
        dist = min(dh, 1.0 - dh) * 2.0;
    } else if (mode == 3) {
        dist = length(chroma2(c) - chroma2(k));
    } else {
        dist = length(c - k) / sqrt(3.0);
    }
    var m = 1.0 - clamp((dist - P.tolerance / 100.0) / max(P.softness / 100.0, 1e-4), 0.0, 1.0);
    if (P.invert > 0.5) {
        m = 1.0 - m;
    }
    if (i32(round(P.view)) == 2) {
        return out8(vec3<f32>(m), s.a);
    }
    let h = fract(hueOf(c) + P.hue / 360.0 + 1.0);
    let sat = clamp(satOf(c) + P.sat / 100.0, 0.0, 1.0);
    let l = clamp(lumHsl(c) + P.light / 100.0, 0.0, 1.0);
    let o = clamp(hslToRgb(h, sat, l), vec3<f32>(0.0), vec3<f32>(1.0));
    return out8(mix(c, o, m), s.a);
}
