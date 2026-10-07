// After Effects "Levels (Individual Controls)" (ADBE Pro Levels2) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The measured Levels law (ae_levels.wgsl) with one set of values per channel: each of R, G, B goes through its own
// channel's levels first, then through the RGB (master) levels; alpha through the Alpha levels. Values are AE's raw
// 0..1 units. The order (channel then master) is an assumption, as are the parameter indices: the channel groups
// (RGB, Red, Green, Blue, Alpha) are taken to start at 3, 9, 15, 21, 27 with their 5 values after the group.
struct Params {
    size: vec2<f32>,
    channel: f32,         // AE 1 Channel (display only)
    rgbInBlack: f32,      // AE 4 RGB Input Black
    rgbInWhite: f32,      // AE 5 RGB Input White
    rgbGamma: f32,        // AE 6 RGB Gamma
    rgbOutBlack: f32,     // AE 7 RGB Output Black
    rgbOutWhite: f32,     // AE 8 RGB Output White
    redInBlack: f32,      // AE 11 Red Input Black
    redInWhite: f32,      // AE 12 Red Input White
    redGamma: f32,        // AE 13 Red Gamma
    redOutBlack: f32,     // AE 14 Red Output Black
    redOutWhite: f32,     // AE 15 Red Output White
    greenInBlack: f32,    // AE 18 Green Input Black
    greenInWhite: f32,    // AE 19 Green Input White
    greenGamma: f32,      // AE 20 Green Gamma
    greenOutBlack: f32,   // AE 21 Green Output Black
    greenOutWhite: f32,   // AE 22 Green Output White
    blueInBlack: f32,     // AE 25 Blue Input Black
    blueInWhite: f32,     // AE 26 Blue Input White
    blueGamma: f32,       // AE 27 Blue Gamma
    blueOutBlack: f32,    // AE 28 Blue Output Black
    blueOutWhite: f32,    // AE 29 Blue Output White
    alphaInBlack: f32,    // AE 32 Alpha Input Black
    alphaInWhite: f32,    // AE 33 Alpha Input White
    alphaGamma: f32,      // AE 34 Alpha Gamma
    alphaOutBlack: f32,   // AE 35 Alpha Output Black
    alphaOutWhite: f32,   // AE 36 Alpha Output White
    clipBlack: f32,       // AE 38 Clip To Output Black (1 Off, 2 On, 3 Off for 32 bpc = on here)
    clipWhite: f32,       // AE 39 Clip To Output White
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

fn lev(c: f32, ib: f32, iw: f32, g: f32, ob: f32, ow: f32) -> f32 {
    var t = (c - ib) / select(iw - ib, 1e-6, abs(iw - ib) < 1e-6);
    let cb = i32(round(P.clipBlack)) != 1;
    let cw = i32(round(P.clipWhite)) != 1;
    if (cb) {
        t = max(t, 0.0);
    }
    if (cw) {
        t = min(t, 1.0);
    }
    let ig = 1.0 / max(g, 1e-4);
    var v: f32;
    if (t < 0.0) {
        v = -pow(-t, ig);
    } else {
        v = pow(t, ig);
    }
    return ob + (ow - ob) * v;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    var o = vec3<f32>(lev(c.r, P.redInBlack, P.redInWhite, P.redGamma, P.redOutBlack, P.redOutWhite),
                      lev(c.g, P.greenInBlack, P.greenInWhite, P.greenGamma, P.greenOutBlack, P.greenOutWhite),
                      lev(c.b, P.blueInBlack, P.blueInWhite, P.blueGamma, P.blueOutBlack, P.blueOutWhite));
    o = vec3<f32>(lev(o.r, P.rgbInBlack, P.rgbInWhite, P.rgbGamma, P.rgbOutBlack, P.rgbOutWhite),
                  lev(o.g, P.rgbInBlack, P.rgbInWhite, P.rgbGamma, P.rgbOutBlack, P.rgbOutWhite),
                  lev(o.b, P.rgbInBlack, P.rgbInWhite, P.rgbGamma, P.rgbOutBlack, P.rgbOutWhite));
    let a = lev(s.a, P.alphaInBlack, P.alphaInWhite, P.alphaGamma, P.alphaOutBlack, P.alphaOutWhite);
    return out8(o, a);
}
