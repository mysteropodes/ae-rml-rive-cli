// After Effects "Drop Shadow" (ADBE Drop Shadow) — the layer alpha, offset by an INTEGER pixel vector, blurred,
// tinted with the shadow colour, scaled by Opacity/255 and composited BEHIND the original (or alone: Shadow Only).
// Measured against AE 2026 8 bpc:
//   * offset = (trunc(d·sin θ), trunc(−d·cos θ)) — θ in degrees from up, clockwise; the vector is truncated toward
//     zero (C int cast): 135°/15 px -> (10, 10), 45°/25 px -> (17, −17), no sub-pixel shift;
//   * Softness s = three successive box blurs (separable, horizontal then vertical) of FRACTIONAL radius
//     r = 0.1846·s (taps −n..n weight 1, ±(n+1) weight f, r = n + f) — fits AE's profiles to ≤ 1 level
//     (s = 30 exact, r ∈ [5.534, 5.542]);
//   * shadow alpha = Opacity/255 · blur(alpha); output = original + shadow·(1 − original alpha) (premultiplied).
// 2 passes: pass 0 = horizontal 3-box blur of the shifted alpha, stored as 16 bits packed in R,G (Rive keeps the
// intermediate pass in RGBA8); pass 1 = vertical 3-box blur + tint + composite with origTex. The 3-box kernel is
// evaluated in closed form (B³ with B = U + f·E), so one pass per axis. Above MAX_TAPS taps per axis (s ≳ 220 px)
// the taps are strided (approximation).
struct Params {
    size: vec2<f32>,
    pad0: vec2<f32>,
    color: vec4<f32>,     // AE 1 Shadow Color (straight color 0..1, alpha ignored)
    opacity: f32,         // AE 2 Opacity 0..255 (default 127.5)
    direction: f32,       // AE 3 Direction degrees (0 = up, clockwise; default 135)
    distance: f32,        // AE 4 Distance px 0..4000 (default 5)
    softness: f32,        // AE 5 Softness px 0..30000 (default 0)
    shadowOnly: f32,      // AE 6 Shadow Only checkbox 0/1
    passIndex: f32,
    pad1: f32,
    pad2: f32,
};
@group(0) @binding(0) var srcTex: texture_2d<f32>;
@group(0) @binding(1) var srcSamp: sampler;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var origTex: texture_2d<f32>;

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

const MAX_TAPS: i32 = 255;
const RADIUS_PER_SOFTNESS: f32 = 0.1846;

fn c2(x: f32) -> f32 {                       // binomial(x, 2), 0 below 2
    return select(0.0, x * (x - 1.0) * 0.5, x >= 2.0);
}

// three convolved boxes of radius r = n + f (unnormalised; divide by (2n+1+2f)^3)
fn k3(j: f32, n: f32, f: f32) -> f32 {
    let m = 2.0 * n + 1.0;
    let q = n + 1.0;
    let aj = abs(j);
    let t = j + 3.0 * n + 2.0;
    var v = c2(t) - 3.0 * c2(t - m) + 3.0 * c2(t - 2.0 * m) - c2(t - 3.0 * m);
    v += 3.0 * f * (max(0.0, m - abs(j + q)) + max(0.0, m - abs(j - q)));
    let u0 = select(0.0, 1.0, aj <= n);
    let um = select(0.0, 1.0, abs(j + 2.0 * q) <= n);
    let up = select(0.0, 1.0, abs(j - 2.0 * q) <= n);
    v += 3.0 * f * f * (um + 2.0 * u0 + up);
    let e3 = select(0.0, 1.0, aj == 3.0 * q) + 3.0 * select(0.0, 1.0, aj == q);
    v += f * f * f * e3;
    return v;
}

fn shadowOffset() -> vec2<i32> {
    let th = radians(P.direction);
    return vec2<i32>(i32(trunc(P.distance * sin(th))), i32(trunc(-P.distance * cos(th))));
}

fn alphaAt(p: vec2<i32>, dims: vec2<i32>) -> f32 {
    if (p.x < 0 || p.y < 0 || p.x >= dims.x || p.y >= dims.y) {
        return 0.0;
    }
    return textureLoad(srcTex, p, 0).a;
}

fn packedAt(p: vec2<i32>, dims: vec2<i32>) -> f32 {
    if (p.x < 0 || p.y < 0 || p.x >= dims.x || p.y >= dims.y) {
        return 0.0;
    }
    let t = textureLoad(srcTex, p, 0);
    return (round(t.r * 255.0) * 256.0 + round(t.g * 255.0)) / 65535.0;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let px = vec2<i32>(floor(in.uv * vec2<f32>(dims)));
    let o = textureSampleLevel(origTex, srcSamp, in.uv, 0.0);   // texel centre -> exact original (premultiplied)
    let off = shadowOffset();
    let r = max(P.softness, 0.0) * RADIUS_PER_SOFTNESS;
    let n = floor(r);
    let f = r - n;
    let R = i32(3.0 * n + select(0.0, 3.0, f > 0.0));
    let span = 2 * R + 1;
    let stp = max(1, (span + MAX_TAPS - 1) / MAX_TAPS);
    let norm = pow(2.0 * n + 1.0 + 2.0 * f, 3.0);
    let horizontal = P.passIndex < 0.5;
    var acc = 0.0;
    var wsum = 0.0;
    var j = -R + (stp - 1) / 2;
    loop {
        if (j > R) { break; }
        let w = k3(f32(j), n, f);
        if (horizontal) {
            acc += w * alphaAt(vec2<i32>(px.x - off.x - j, px.y), dims);
        } else {
            acc += w * packedAt(vec2<i32>(px.x, px.y - off.y - j), dims);
        }
        wsum += w;
        j += stp;
    }
    var v = 0.0;
    if (stp == 1) {
        v = acc / norm;
    } else if (wsum > 0.0) {
        v = acc / wsum;
    }
    v = clamp(v, 0.0, 1.0);
    if (horizontal) {
        let q = round(v * 65535.0);
        let hi = floor(q / 256.0);
        return vec4<f32>(hi / 255.0, (q - hi * 256.0) / 255.0, 0.0, 1.0);
    }
    let sa = clamp(P.opacity / 255.0, 0.0, 1.0) * v;
    let shadow = vec4<f32>(clamp(P.color.rgb, vec3<f32>(0.0), vec3<f32>(1.0)) * sa, sa);
    if (P.shadowOnly > 0.5) {
        return shadow;
    }
    return o + shadow * (1.0 - o.a);
}
