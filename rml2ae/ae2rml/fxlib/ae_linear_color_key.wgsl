// After Effects "Linear Color Key" (ADBE Linear Color Key2) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Distance to Key Color (Match colors 1 Using RGB, 2 Using Hue, 3 Using Chroma), a matte 0 below Matching Tolerance
// ramping to 1 over Matching Softness; Key Operation 1 Key Colors removes the matched pixels, 2 Keep Colors keeps
// only them. View 2 shows the matte. Indices checked against AE 26 (1 = the preview group).
struct Params {
    size: vec2<f32>,
    view: f32,            // AE 2 View (menu)
    matchMode: f32,       // AE 4 Match colors (menu)
    key: vec4<f32>,       // AE 3 Key Color
    tolerance: f32,       // AE 5 Matching Tolerance (%)
    softness: f32,        // AE 6 Matching Softness (%)
    operation: f32,       // AE 7 Key Operation (menu)
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
    let k = P.key.rgb;
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
    var m = clamp((dist - P.tolerance / 100.0) / max(P.softness / 100.0, 1e-4), 0.0, 1.0);
    if (i32(round(P.operation)) == 2) {
        m = 1.0 - m;
    }
    if (i32(round(P.view)) == 2) {
        return out8(vec3<f32>(m), s.a);
    }
    return out8(c, s.a * m);
}
