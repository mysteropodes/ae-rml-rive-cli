// After Effects "Noise HLS" (ADBE Noise HLS2) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Random noise added in HLS: Hue, Lightness and Saturation amounts in % (hue as a fraction of the circle); Noise 1
// Uniform, 2 Squared, 3 Grain (blocks of Grain Size px). The random draw differs from After Effects; Noise Phase
// shifts it. Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    kind: f32,            // AE 1 Noise (menu)
    hue: f32,             // AE 2 Hue (%)
    light: f32,           // AE 3 Lightness (%)
    sat: f32,             // AE 4 Saturation (%)
    grain: f32,           // AE 5 Grain Size
    phase: f32,           // AE 6 Noise Phase
    passIndex: f32,
    pad0: f32,
    layerRect: vec4<f32>, // reserved, filled by the host: the layer's rect in the canvas (x0, y0, x1, y1)
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

// layer px of the centre of this fragment's canvas pixel
fn layerPos(uv: vec2<f32>) -> vec2<f32> {
    return vec2<f32>(pixelOf(uv)) + vec2<f32>(0.5) - P.layerRect.xy;
}

fn layerSize() -> vec2<f32> {
    return max(P.layerRect.zw - P.layerRect.xy, vec2<f32>(1.0));
}

// bilinear sample of the layer at layer px q (pixel i centre = i + 0.5), premultiplied, transparent outside the layer
fn tapL(q: vec2<f32>) -> vec4<f32> {
    let size = layerSize();
    let p = q - vec2<f32>(0.5);
    let o = max(-p, p - (size - vec2<f32>(1.0)));
    let cov = clamp(vec2<f32>(1.0) - o, vec2<f32>(0.0), vec2<f32>(1.0));
    let c = cov.x * cov.y;
    let dims = vec2<f32>(textureDimensions(srcTex, 0));
    if (c <= 0.0) {
        return vec4<f32>(0.0);
    }
    return c * textureSampleLevel(srcTex, srcSamp, (q + P.layerRect.xy) / dims, 0.0);
}

// a point parameter (the host writes it in canvas px, i.e. layer px + fxPad) -> layer px
fn layerPt(c: vec2<f32>) -> vec2<f32> {
    return c - P.layerRect.xy;
}

// AE angle convention: 0 = up, clockwise (y down)
fn aeDir(deg: f32) -> vec2<f32> {
    let a = radians(deg);
    return vec2<f32>(sin(a), -cos(a));
}

// deterministic per-cell random in [0, 1) (After Effects' own generator is not public: patterns differ from AE)
fn hash21(p: vec2<f32>, seed: f32) -> f32 {
    let q = fract(vec3<f32>(p.x, p.y, seed) * vec3<f32>(0.1031, 0.1030, 0.0973));
    let r = q + dot(q, q.yzx + vec3<f32>(33.33));
    return fract((r.x + r.y) * r.z);
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    var cell = floor(layerPos(in.uv));
    let mode = i32(round(P.kind));
    if (mode == 3) {
        cell = floor(cell / max(P.grain, 1.0));
    }
    var n = vec3<f32>(hash21(cell, 41.0 + P.phase), hash21(cell, 42.0 + P.phase), hash21(cell, 43.0 + P.phase)) - vec3<f32>(0.5);
    if (mode == 2) {
        n = sign(n) * n * n * 2.0;
    }
    let h = fract(hueOf(c) + n.x * P.hue / 100.0 + 1.0);
    let l = clamp(lumHsl(c) + n.y * P.light / 100.0, 0.0, 1.0);
    let sa = clamp(satOf(c) + n.z * P.sat / 100.0, 0.0, 1.0);
    return out8(hslToRgb(h, sa, l), s.a);
}
