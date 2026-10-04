// After Effects "Brightness & Contrast" (ADBE Brightness & Contrast 2, FR "Luminosite et contraste"), non-legacy
// mode = Photoshop's curve-based Brightness/Contrast (copied from Camera Raw PV2003). Measured vs AE 2026 (8 bpc):
// a per-channel tone curve on the STRAIGHT 8-bit colour (alpha untouched), brightness curve first, then contrast:
//   Brightness B > 0: m = exp(0.0063 * B); y = m * x up to y = 0.5 (x0 = 0.5 / m), then a cubic Hermite shoulder from
//     (x0, 0.5) with slope m to (1, 1) with slope s1 = 1 / (1 + 12 (m - 1)).
//   Brightness B < 0: the inverse function of the B = |B| curve (y = x / m up to x = 0.5, then the inverted shoulder).
//   Contrast C: a = 0.0076 * C; per half, a parabola symmetric about 0.5: lower half u = 2x,
//     y = ((1 - a) u + a u^2) / 2; upper half mirrored (y = 1 - f(1 - x)).
//   One rounding (half up). Exact on the three visible references except 1 LUT entry (+-1).
// Use Legacy (AE 3 = 1): Photoshop's legacy formula, NOT measured: y = x + B/255, then contrast about 0.5 with
//   factor 1 + C/100 (C < 0) or 1 / (1 - C/100) (C > 0), clamped.
struct Params {
    size: vec2<f32>,
    brightness: f32,      // AE 1 Brightness -150..150
    contrast: f32,        // AE 2 Contrast -100..100
    legacy: f32,          // AE 3 Use Legacy 0/1
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

// the B > 0 shoulder: cubic Hermite (x0, 0.5, slope m) -> (1, 1, slope s1)
fn shoulder(x: f32, x0: f32, m: f32, s1: f32) -> f32 {
    let h = 1.0 - x0;
    let u = (x - x0) / h;
    let u2 = u * u;
    let u3 = u2 * u;
    return (2.0 * u3 - 3.0 * u2 + 1.0) * 0.5 + (u3 - 2.0 * u2 + u) * h * m + (-2.0 * u3 + 3.0 * u2)
         + (u3 - u2) * h * s1;
}

fn brightCurve(x: f32, b: f32) -> f32 {
    if (abs(b) < 1e-6) {
        return x;
    }
    let m = exp(0.0063 * abs(b));
    let s1 = 1.0 / (1.0 + 12.0 * (m - 1.0));
    let x0 = 0.5 / m;
    if (b > 0.0) {
        if (x <= x0) {
            return m * x;
        }
        return shoulder(x, x0, m, s1);
    }
    // inverse curve
    if (x <= 0.5) {
        return x / m;
    }
    var lo = x0;
    var hi = 1.0;
    for (var i = 0; i < 30; i = i + 1) {
        let mid = 0.5 * (lo + hi);
        if (shoulder(mid, x0, m, s1) < x) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    return 0.5 * (lo + hi);
}

fn contrastCurve(x: f32, c: f32) -> f32 {
    let a = 0.0076 * c;
    if (x <= 0.5) {
        let u = 2.0 * x;
        return 0.5 * ((1.0 - a) * u + a * u * u);
    }
    let u = 2.0 * (1.0 - x);
    return 1.0 - 0.5 * ((1.0 - a) * u + a * u * u);
}

fn curve(x: f32) -> f32 {
    if (P.legacy >= 0.5) {
        var y = clamp(x + P.brightness / 255.0, 0.0, 1.0);
        var f = 1.0 + P.contrast * 0.01;
        if (P.contrast > 0.0) {
            f = 1.0 / max(1.0 - P.contrast * 0.01, 1e-4);
        }
        return (y - 0.5) * f + 0.5;
    }
    return contrastCurve(clamp(brightCurve(x, P.brightness), 0.0, 1.0), P.contrast);
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
    let c = floor(clamp(s.rgb / s.a, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5) / 255.0;   // straight 8-bit
    let y = vec3<f32>(curve(c.r), curve(c.g), curve(c.b));
    let rgb = floor(clamp(y, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5) / 255.0;
    return vec4<f32>(rgb * s.a, s.a);
}
