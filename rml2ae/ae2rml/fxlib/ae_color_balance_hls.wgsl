// After Effects "Color Balance (HLS)" (ADBE Color Balance (HLS), FR "Balance des couleurs (TLS)"). Measured vs AE 2026
// (8 bpc) on the STRAIGHT 8-bit colour, HLS space with L = (max + min) / 2:
//   Hue: HLS hue rotation keeping max and min (+90 deg: exact except exact .5 ties, which AE rounds either way).
//   Saturation: HLS S' = S (1 + Sat/100) for Sat < 0, S + (1 - S) Sat/100 for Sat > 0 (= chroma scaled about L).
//   Lightness: > 0 blends the RGB towards white (c + (1 - c) * Lt), < 0 towards black (c * (1 + Lt)), Lt = Light/100
//     (NOT an HLS L shift: shadows lose chroma like the highlights; exact on the reference).
//   One rounding at the end, exact .5 ties DOWN (Lightness +30: 0 -> 76.5 -> 76); alpha untouched.
struct Params {
    size: vec2<f32>,
    hue: f32,             // AE 1 Hue, degrees
    lightness: f32,       // AE 2 Lightness -100..100
    saturation: f32,      // AE 3 Saturation -100..100
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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

// standard hue (0..1, 0 = red) of a straight colour; 0 for greys
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

// pure-hue channel weights 0..1 of hue h
fn hueRgb(h: f32) -> vec3<f32> {
    return clamp(abs(fract(vec3<f32>(h) + vec3<f32>(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - vec3<f32>(3.0)) - vec3<f32>(1.0),
                 vec3<f32>(0.0), vec3<f32>(1.0));
}

fn hlsToRgb(h: f32, l: f32, s: f32) -> vec3<f32> {
    var q: f32;
    if (l <= 0.5) {
        q = l * (1.0 + s);
    } else {
        q = l + s - l * s;
    }
    let p = 2.0 * l - q;
    return vec3<f32>(p) + (q - p) * hueRgb(h);
}

fn lighten(c: vec3<f32>, light: f32) -> vec3<f32> {
    let lt = clamp(light * 0.01, -1.0, 1.0);
    if (lt >= 0.0) {
        return c + (vec3<f32>(1.0) - c) * lt;
    }
    return c * (1.0 + lt);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let ip = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0), dims - vec2<i32>(1));
    let keep = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0).a * 0.0;   // keeps binding 1 in the auto layout
    let s = textureLoad(srcTex, ip, 0) + vec4<f32>(keep);  // premultiplied
    if (s.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    var c = floor(clamp(s.rgb / s.a, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5) / 255.0;   // straight 8-bit
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let l = 0.5 * (mx + mn);
    if (mx > mn) {
        var sat: f32;
        if (l <= 0.5) {
            sat = (mx - mn) / (mx + mn);
        } else {
            sat = (mx - mn) / (2.0 - mx - mn);
        }
        let rot = fract(P.hue / 360.0 + 1000.0);
        if (rot != 0.0) {
            c = vec3<f32>(mn) + (mx - mn) * hueRgb(fract(hueOf(c) + rot));   // keeps max / min
        }
        // Sat < 0: S * (1 + Sat/100); Sat > 0: S + (1 - S) * Sat/100 (held-out ref -45°/+40: the product law was off
        // by up to 40/255); both = chroma scaled about L by k = S' / S (pure greys stay grey: mx > mn above)
        let ks = clamp(P.saturation * 0.01, -1.0, 1.0);
        var s2 = sat * (1.0 + ks);
        if (ks > 0.0) {
            s2 = sat + (1.0 - sat) * ks;
        }
        let k = clamp(s2, 0.0, 1.0) / max(sat, 1e-6);
        c = vec3<f32>(l) + (c - vec3<f32>(l)) * k;
    }
    c = lighten(c, P.lightness);
    // exact .5 ties go down in AE (f32 noise margin 5e-4 of a level)
    let rgb = floor(clamp(c, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.4995) / 255.0;
    return vec4<f32>(rgb * s.a, s.a);
}
