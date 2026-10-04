// After Effects "Levels" (ADBE Easy Levels2). Measured against AE 2026 (8 bpc, no colour management), exact on every
// opaque input level (0 mismatches over the full 0..255 LUT in the three reference settings) and on the
// semi-transparent pixels:
//   c   = straight colour 0..1 (the layer is un-premultiplied first, re-premultiplied at the end; alpha untouched)
//   t   = (c - Input Black) / (Input White - Input Black)          (levels values are 0..1 in AE's raw units)
//   t   = clamp(t, 0, 1)            when clipping is on (8 bpc default "Off for 32 bpc Color" = on in 8/16 bpc)
//   out = Output Black + (Output White - Output Black) * t ^ (1 / Gamma)
// Fully transparent pixels stay 0 (premultiplied output) unless the Alpha channel itself is levelled.
// Channel popup: 1 = RGB (R, G and B), 2 = Red, 3 = Green, 4 = Blue only, 5 = Alpha only (the straight colour is
// kept and re-premultiplied with the new alpha).
// Clipping Off (popup value 1): values below Input Black / above Input White are extrapolated (t < 0 uses a
// sign-preserving power, t > 1 the plain power) -- assumed from AE's documentation, not measured (the 8 bpc canvas
// clamps the final value to 0..1 anyway).
struct Params {
    size: vec2<f32>,
    channel: f32,         // AE 1 Channel popup 1 = RGB, 2 = Red, 3 = Green, 4 = Blue, 5 = Alpha
    inBlack: f32,         // AE 3 Input Black 0..1 (raw; UI 0..255 in 8 bpc), range -10000..10000
    inWhite: f32,         // AE 4 Input White 0..1 (raw; UI 0..255 in 8 bpc), range -10000..10000
    gamma: f32,           // AE 5 Gamma 0..5 (out = t^(1/gamma))
    outBlack: f32,        // AE 6 Output Black 0..1 (raw; UI 0..255 in 8 bpc), range -10000..10000
    outWhite: f32,        // AE 7 Output White 0..1 (raw; UI 0..255 in 8 bpc), range -10000..10000
    clipBlack: f32,       // AE 8 Clip To Output Black popup 1 = Off, 2 = On, 3 = Off for 32 bpc Color (= on here)
    clipWhite: f32,       // AE 9 Clip To Output White popup 1 = Off, 2 = On, 3 = Off for 32 bpc Color (= on here)
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

fn level(x: f32) -> f32 {
    var d = P.inWhite - P.inBlack;
    if (abs(d) < 1e-6) {
        d = select(-1e-6, 1e-6, d >= 0.0);
    }
    var t = (x - P.inBlack) / d;
    let clipB = round(P.clipBlack) != 1.0;
    let clipW = round(P.clipWhite) != 1.0;
    if (clipB) {
        t = max(t, 0.0);
    }
    if (clipW) {
        t = min(t, 1.0);
    }
    let e = 1.0 / max(P.gamma, 1e-4);
    var g = 0.0;                                            // pow(0, e) is undefined in WGSL: keep 0 explicit
    if (t > 0.0) {
        g = pow(t, e);
    } else if (t < 0.0) {
        g = -pow(-t, e);
    }
    return P.outBlack + (P.outWhite - P.outBlack) * g;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureSample(srcTex, srcSamp, in.uv);          // premultiplied
    let ch = i32(round(P.channel));
    var a = s.a;
    var c = vec3<f32>(0.0);
    if (a > 0.0) {
        c = clamp(s.rgb / a, vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (ch == 5) {
        a = clamp(level(a), 0.0, 1.0);
    } else if (a > 0.0) {
        if (ch <= 1 || ch == 2) {
            c.r = level(c.r);
        }
        if (ch <= 1 || ch == 3) {
            c.g = level(c.g);
        }
        if (ch <= 1 || ch == 4) {
            c.b = level(c.b);
        }
        // AE rounds exact halves up (Output White 0.9 -> 229.5 -> 230); f32 lands a hair below: nudge by 1/4000 level
        c = clamp(c + vec3<f32>(1e-6), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    return vec4<f32>(c * a, a);
}
