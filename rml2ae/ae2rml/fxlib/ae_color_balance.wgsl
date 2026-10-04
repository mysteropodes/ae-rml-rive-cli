// After Effects "Color Balance" (ADBE Color Balance 2) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Per channel, v' = v + S * shadows(L) + M * midtones(L) + H * highlights(L), with S/M/H = balance / 100 (-1..1) and
// the tonal weights of the classic colour-balance transfer (a = 0.25, b = 0.333, scale 0.7, L = HSL lightness);
// Preserve Luminosity restores the HSL lightness of the source. Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    sR: f32,              // AE 1 Shadow Red Balance (-100..100)
    sG: f32,              // AE 2 Shadow Green Balance
    sB: f32,              // AE 3 Shadow Blue Balance
    mR: f32,              // AE 4 Midtone Red Balance
    mG: f32,              // AE 5 Midtone Green Balance
    mB: f32,              // AE 6 Midtone Blue Balance
    hR: f32,              // AE 7 Highlight Red Balance
    hG: f32,              // AE 8 Highlight Green Balance
    hB: f32,              // AE 9 Highlight Blue Balance
    preserve: f32,        // AE 10 Preserve Luminosity (checkbox)
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let L = lumHsl(c);
    let a = 0.25;
    let b = 0.333;
    let sc = 0.7;
    let wS = clamp((L - b) / -a + 0.5, 0.0, 1.0) * sc;
    let wM = clamp((L - b) / a + 0.5, 0.0, 1.0) * clamp((L + b - 1.0) / -a + 0.5, 0.0, 1.0) * sc;
    let wH = clamp((L + b - 1.0) / a + 0.5, 0.0, 1.0) * sc;
    var o = c + (vec3<f32>(P.sR, P.sG, P.sB) * wS + vec3<f32>(P.mR, P.mG, P.mB) * wM + vec3<f32>(P.hR, P.hG, P.hB) * wH) / 100.0;
    o = clamp(o, vec3<f32>(0.0), vec3<f32>(1.0));
    if (P.preserve > 0.5) {
        o = clamp(hslToRgb(hueOf(o), satOf(o), L), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    return out8(o, s.a);
}
