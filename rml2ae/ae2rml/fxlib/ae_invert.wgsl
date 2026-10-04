// After Effects "Invert" (ADBE Invert, FR "Inverser") — measured on AE 2026 8 bpc. AE works on the STRAIGHT 8-bit
// colour of each pixel, the output is premultiplied by the (possibly inverted) alpha:
//   RGB / Red / Green / Blue: c' = 1 - c on the chosen channels (bit-exact vs AE).
//   Hue: the hue is MIRRORED, not rotated: H' = 0.5 - H (mod 1, standard HLS hue, 0 = red) with L and S kept, i.e.
//        min and max of R, G, B kept, red (255,3,0) -> (0,255,252) (bit-exact vs AE over the whole test image;
//        H + 0.5 is off on 70 % of the pixels).
//   HLS / Lightness / Saturation: the same HLS space, components inverted (H' = 0.5 - H, L' = 1 - L, S' = 1 - S);
//        no visible AE reference — the hue of a grey pixel (needed by Saturation / HLS) is taken as 0.
//   YIQ / Luminance / In Phase / Quadrature Chrominance: NTSC YIQ (Y = .299 .587 .114, I = .596 -.274 -.322,
//        Q = .211 -.523 .312); Y' = 1 - Y, I' = -I, Q' = -Q (YIQ = all three = RGB invert); result clamped 0..1.
//        No visible AE reference.
//   Alpha: A' = 1 - A, the straight colour kept (fully transparent pixels have a black straight colour).
// Blend With Original mixes the straight result (and alpha) with the untouched pixel (Blend/100), then 8-bit rounding.
// Popup entries 5, 10 and 15 are AE menu separators (not selectable): pass-through.
struct Params {
    size: vec2<f32>,
    channel: f32,         // AE 1 Channel popup 1..16 (1 RGB, 2 Red, 3 Green, 4 Blue, 6 HLS, 7 Hue, 8 Lightness, 9 Saturation, 11 YIQ, 12 Luminance, 13 In Phase Chrominance, 14 Quadrature Chrominance, 16 Alpha; 5/10/15 separators)
    blend: f32,           // AE 2 Blend With Original 0..100 %
    passIndex: f32,
    pad0: f32,
    pad1: f32,
    pad2: f32,
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

// standard hue (0..1, 0 = red, 1/3 = green, 2/3 = blue) of a straight colour; 0 for greys
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

// pure-hue weights (0..1 per channel) of hue h: the HLS -> RGB channel shape
fn hueRgb(h: f32) -> vec3<f32> {
    return clamp(abs(fract(vec3<f32>(h) + vec3<f32>(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - vec3<f32>(3.0)) - vec3<f32>(1.0),
                 vec3<f32>(0.0), vec3<f32>(1.0));
}

// HLS (h, l, s all 0..1) -> RGB
fn hlsToRgb(h: f32, l: f32, s: f32) -> vec3<f32> {
    var q: f32;
    if (l < 0.5) {
        q = l * (1.0 + s);
    } else {
        q = l + s - l * s;
    }
    let p = 2.0 * l - q;
    return vec3<f32>(p) + (q - p) * hueRgb(h);
}

// straight colour + alpha -> inverted straight colour + alpha, by AE channel popup index
fn invertPx(c: vec3<f32>, a: f32, mode: i32) -> vec4<f32> {
    switch mode {
        case 1: { return vec4<f32>(vec3<f32>(1.0) - c, a); }
        case 2: { return vec4<f32>(1.0 - c.r, c.g, c.b, a); }
        case 3: { return vec4<f32>(c.r, 1.0 - c.g, c.b, a); }
        case 4: { return vec4<f32>(c.r, c.g, 1.0 - c.b, a); }
        case 6, 7, 8, 9: {
            let mx = max(c.r, max(c.g, c.b));
            let mn = min(c.r, min(c.g, c.b));
            let h = hueOf(c);
            if (mode == 7) {
                // hue mirrored, min/max kept (exact form of HLS with L and S unchanged)
                return vec4<f32>(vec3<f32>(mn) + (mx - mn) * hueRgb(fract(1.5 - h)), a);
            }
            let l = 0.5 * (mx + mn);
            let d = mx - mn;
            var s = 0.0;
            if (d > 0.0) {
                if (l <= 0.5) {
                    s = d / (mx + mn);
                } else {
                    s = d / (2.0 - mx - mn);
                }
            }
            var h2 = h;
            var l2 = l;
            var s2 = s;
            if (mode == 6) {
                h2 = fract(1.5 - h);
                l2 = 1.0 - l;
                s2 = 1.0 - s;
            } else if (mode == 8) {
                l2 = 1.0 - l;
            } else {
                s2 = 1.0 - s;
            }
            return vec4<f32>(clamp(hlsToRgb(h2, l2, s2), vec3<f32>(0.0), vec3<f32>(1.0)), a);
        }
        case 11, 12, 13, 14: {
            let y = dot(c, vec3<f32>(0.299, 0.587, 0.114));
            let i = dot(c, vec3<f32>(0.596, -0.274, -0.322));
            let q = dot(c, vec3<f32>(0.211, -0.523, 0.312));
            var y2 = y;
            var i2 = i;
            var q2 = q;
            if (mode == 11 || mode == 12) {
                y2 = 1.0 - y;
            }
            if (mode == 11 || mode == 13) {
                i2 = -i;
            }
            if (mode == 11 || mode == 14) {
                q2 = -q;
            }
            // inverse of the YIQ matrix above
            let rgb = vec3<f32>(y2) + i2 * vec3<f32>(0.956170685, -0.272688602, -1.103744082)
                                   + q2 * vec3<f32>(0.621432566, -0.646813237, 1.700623095);
            return vec4<f32>(clamp(rgb, vec3<f32>(0.0), vec3<f32>(1.0)), a);
        }
        case 16: { return vec4<f32>(c, 1.0 - a); }
        default: { return vec4<f32>(c, a); }
    }
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    let ip = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dimi))), vec2<i32>(0), dimi - vec2<i32>(1));
    let s = textureLoad(srcTex, ip, 0);                     // premultiplied
    let a = s.a;
    var c = vec3<f32>(0.0);
    if (a > 0.0) {
        c = clamp(round(s.rgb / a * 255.0) / 255.0, vec3<f32>(0.0), vec3<f32>(1.0));   // AE's straight 8-bit colour
    }
    let inv = invertPx(c, a, i32(round(P.channel)));
    let b = clamp(P.blend / 100.0, 0.0, 1.0);
    let o = mix(inv, vec4<f32>(c, a), b);
    let oc = round(clamp(o.rgb, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0) / 255.0;      // AE 8 bpc result
    let oa = round(clamp(o.a, 0.0, 1.0) * 255.0) / 255.0;
    return vec4<f32>(oc * oa, oa);
}
