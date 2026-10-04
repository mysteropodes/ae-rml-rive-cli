// After Effects "Hue/Saturation" (ADBE HUE SATURATION, FR "Teinte/Saturation") — Master channel only. Measured vs
// AE 2026 (8 bpc) on the STRAIGHT 8-bit colour (L = (max + min) / 2, the HLS lightness):
//   Master Hue: HLS hue rotation that keeps max and min (+120 deg = (r,g,b) -> (b,r,g), bit-exact).
//   Master Saturation < 0: chroma scaled about L by k = 1 + Sat * 2.55 / 256 (AE's 8-bit step: -60 -> 103/256, NOT
//     0.4 — 0.4 is off by 1 on 20 % of the pixels; 103/256 is exact on 99.98 %).
//   Master Saturation > 0: Photoshop's formula (no visible reference): inc = Sat * 2.55 / 256, S = HLS saturation;
//     a = (inc + S >= 1) ? S : 1 - inc; rgb += (rgb - L) * (1 / a - 1).
//   Master Lightness: > 0 blends towards white (c + (1 - c) * Lt), < 0 towards black (c * (1 + Lt)), Lt = Light/100.
//   Colorize (AE 6 = 1): out = HLS(Colorize Hue, Colorize Saturation / 100, L) — bit-exact; Colorize Lightness is
//     applied like Master Lightness (no visible reference).
//   One rounding (half up) at the end; alpha untouched.
// AE 1 Channel Control (Reds/Yellows/... ranges) and AE 2 Channel Range are not reproduced: only the Master values.
struct Params {
    size: vec2<f32>,
    channelControl: f32,  // AE 1 Channel Control popup (1 = Master; others not reproduced)
    masterHue: f32,       // AE 3 Master Hue, degrees
    masterSat: f32,       // AE 4 Master Saturation -100..100
    masterLight: f32,     // AE 5 Master Lightness -100..100
    colorize: f32,        // AE 6 Colorize checkbox 0/1
    colorizeHue: f32,     // AE 7 Colorize Hue, degrees
    colorizeSat: f32,     // AE 8 Colorize Saturation 0..100
    colorizeLight: f32,   // AE 9 Colorize Lightness -100..100
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
    if (P.colorize >= 0.5) {
        c = hlsToRgb(fract(P.colorizeHue / 360.0 + 1000.0), l, clamp(P.colorizeSat * 0.01, 0.0, 1.0));
        c = lighten(c, P.colorizeLight);
    } else {
        // hue rotation keeping max / min
        let rot = fract(P.masterHue / 360.0 + 1000.0);
        if (rot != 0.0 && mx > mn) {
            c = vec3<f32>(mn) + (mx - mn) * hueRgb(fract(hueOf(c) + rot));
        }
        let inc = clamp(P.masterSat, -100.0, 100.0) * 2.55 / 256.0;
        if (inc < 0.0) {
            c = vec3<f32>(l) + (c - vec3<f32>(l)) * (1.0 + inc);
        } else if (inc > 0.0 && mx > mn) {
            var sat: f32;
            if (l <= 0.5) {
                sat = (mx - mn) / (mx + mn);
            } else {
                sat = (mx - mn) / (2.0 - mx - mn);
            }
            var a = 1.0 - inc;
            if (inc + sat >= 1.0) {
                a = sat;
            }
            c = c + (c - vec3<f32>(l)) * (1.0 / max(a, 1e-6) - 1.0);
        }
        c = lighten(c, P.masterLight);
    }
    let rgb = floor(clamp(c, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5) / 255.0;
    return vec4<f32>(rgb * s.a, s.a);
}
