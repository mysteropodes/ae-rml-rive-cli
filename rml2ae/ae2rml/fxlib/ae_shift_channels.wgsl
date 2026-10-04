// After Effects "Shift Channels" (ADBE Shift Channels, FR "Décalage des couches") — measured on AE 2026 8 bpc.
// Each output channel (alpha, red, green, blue) takes its value from a source of the STRAIGHT 8-bit pixel; the new
// straight colour is then premultiplied by the new alpha (fully transparent pixels have a black straight colour).
// Popup 1..10: 1 Alpha, 2 Red, 3 Green, 4 Blue, 5 Luminance, 6 Hue, 7 Lightness, 8 Saturation, 9 Full, 10 Off.
//   * channel swaps (Red <- Green, ...) bit-exact vs AE;
//   * Luminance = round(255 * (0.299 R + 0.587 G + 0.114 B)) (Rec.601, 8-bit rounded) — bit-exact vs AE (alpha
//     taken from Luminance);
//   * Hue / Lightness / Saturation = standard HLS components scaled to 0..255 and rounded (hue of greys = 0),
//     Full = 255, Off = 0: menu order and formulas not covered by a visible reference.
struct Params {
    size: vec2<f32>,
    takeAlpha: f32,       // AE 1 Take Alpha From 1..10 (default 1 Alpha)
    takeRed: f32,         // AE 2 Take Red From 1..10 (default 2 Red)
    takeGreen: f32,       // AE 3 Take Green From 1..10 (default 3 Green)
    takeBlue: f32,        // AE 4 Take Blue From 1..10 (default 4 Blue)
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

// HLS of a straight colour (0..1): hue 0..1 (0 = red, 0 for greys), lightness, saturation
fn hls(c: vec3<f32>) -> vec3<f32> {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let l = 0.5 * (mx + mn);
    let d = mx - mn;
    if (d <= 0.0) {
        return vec3<f32>(0.0, l, 0.0);
    }
    var s: f32;
    if (l <= 0.5) {
        s = d / (mx + mn);
    } else {
        s = d / (2.0 - mx - mn);
    }
    var h: f32;
    if (mx == c.r) {
        h = (c.g - c.b) / d;
    } else if (mx == c.g) {
        h = 2.0 + (c.b - c.r) / d;
    } else {
        h = 4.0 + (c.r - c.g) / d;
    }
    return vec3<f32>(fract(h / 6.0 + 1.0), l, s);
}

// value 0..255 of source `mode` for straight 8-bit colour c8 / alpha a8
fn pick(mode: i32, c8: vec3<f32>, a8: f32) -> f32 {
    switch mode {
        case 1: { return a8; }
        case 2: { return c8.r; }
        case 3: { return c8.g; }
        case 4: { return c8.b; }
        case 5: { return floor(dot(c8, vec3<f32>(0.299, 0.587, 0.114)) + 0.5); }
        case 6: { return floor(hls(c8 / 255.0).x * 255.0 + 0.5); }
        case 7: { return floor(hls(c8 / 255.0).y * 255.0 + 0.5); }
        case 8: { return floor(hls(c8 / 255.0).z * 255.0 + 0.5); }
        case 9: { return 255.0; }
        default: { return 0.0; }
    }
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    let ip = clamp(vec2<i32>(floor(in.pos.xy)), vec2<i32>(0), dimi - vec2<i32>(1));
    let keep = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0).a * 0.0;   // keeps binding 1 in the auto layout
    let s = textureLoad(srcTex, ip, 0) + vec4<f32>(keep);                 // premultiplied
    let a8 = round(s.a * 255.0);
    var c8 = vec3<f32>(0.0);
    if (s.a > 0.0) {
        c8 = clamp(round(s.rgb / s.a * 255.0), vec3<f32>(0.0), vec3<f32>(255.0));   // AE's straight 8-bit colour
    }
    let na = pick(i32(round(P.takeAlpha)), c8, a8);
    let nc = vec3<f32>(pick(i32(round(P.takeRed)), c8, a8), pick(i32(round(P.takeGreen)), c8, a8),
                       pick(i32(round(P.takeBlue)), c8, a8));
    let pm = floor((nc * na + 127.0) / 255.0);                            // AE 8-bit premultiply
    return vec4<f32>(pm / 255.0, na / 255.0);
}
