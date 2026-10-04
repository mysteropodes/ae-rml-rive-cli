// After Effects "Colorama" (APC Colorama) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Parameter positions measured (fxlib/_ae_params.json). A phase 0..1 is taken from the pixel (Get Phase From, assumed
// menu: 1 Intensity, 2 Red, 3 Green, 4 Blue, 5 Hue, 6 Lightness, 7 Saturation, 8 Value, 9 Alpha, 10 Zero), shifted by
// Phase Shift (degrees / 360), repeated Cycle Repetitions times, and mapped through the output cycle. The custom
// Output Cycle wheel (custom data) is not read: every preset is taken as the default Hue Cycle (the hue circle from
// red). Modify (1 All Channels, 2 Hue, 3 Lightness, 4 Saturation; others = All) chooses what the cycle replaces;
// Composite Over Layer keeps the layer's alpha; Blend With Original mixes the result back. Add Phase, Pixel Selection
// and Masking are not modelled.
struct Params {
    size: vec2<f32>,
    phaseFrom: f32,       // AE 2 Get Phase From (menu)
    shift: f32,           // AE 6 Phase Shift (degrees)
    preset: f32,          // AE 9 Use Preset Palette (menu)
    repeats: f32,         // AE 11 Cycle Repetitions
    modify: f32,          // AE 15 Modify (menu)
    modAlpha: f32,        // AE 16 Modify Alpha
    composite: f32,       // AE 29 Composite Over Layer
    original: f32,        // AE 30 Blend With Original (%)
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

fn rgb2hls(c: vec3<f32>) -> vec3<f32> {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let l = 0.5 * (mx + mn);
    let d = mx - mn;
    if (d <= 1e-6) {
        return vec3<f32>(0.0, l, 0.0);
    }
    var s = d / (mx + mn);
    if (l > 0.5) {
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

fn hue2c(p: f32, q: f32, t0: f32) -> f32 {
    let t = fract(t0 + 1.0);
    if (t < 1.0 / 6.0) {
        return p + (q - p) * 6.0 * t;
    }
    if (t < 0.5) {
        return q;
    }
    if (t < 2.0 / 3.0) {
        return p + (q - p) * (2.0 / 3.0 - t) * 6.0;
    }
    return p;
}

fn hls2rgb(x: vec3<f32>) -> vec3<f32> {
    let h = x.x;
    let l = clamp(x.y, 0.0, 1.0);
    let s = clamp(x.z, 0.0, 1.0);
    if (s <= 0.0) {
        return vec3<f32>(l);
    }
    var q = l + s - l * s;
    if (l < 0.5) {
        q = l * (1.0 + s);
    }
    let p = 2.0 * l - q;
    return vec3<f32>(hue2c(p, q, h + 1.0 / 3.0), hue2c(p, q, h), hue2c(p, q, h - 1.0 / 3.0));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let x = rgb2hls(c);
    let pf = i32(round(P.phaseFrom));
    var ph = dot(c, vec3<f32>(0.299, 0.587, 0.114));
    switch pf {
        case 2: { ph = c.r; }
        case 3: { ph = c.g; }
        case 4: { ph = c.b; }
        case 5: { ph = x.x; }
        case 6: { ph = x.y; }
        case 7: { ph = x.z; }
        case 8: { ph = max(c.r, max(c.g, c.b)); }
        case 9: { ph = s.a; }
        case 10: { ph = 0.0; }
        default: {}
    }
    let t = fract(ph * max(P.repeats, 0.0) + P.shift / 360.0);
    let cyc = hls2rgb(vec3<f32>(t, 0.5, 1.0));
    let md = i32(round(P.modify));
    var o = cyc;
    if (md == 2) {
        o = hls2rgb(vec3<f32>(t, x.y, x.z));
    } else if (md == 3) {
        o = hls2rgb(vec3<f32>(x.x, rgb2hls(cyc).y, x.z));
    } else if (md == 4) {
        o = hls2rgb(vec3<f32>(x.x, x.y, rgb2hls(cyc).z));
    }
    let k = 1.0 - clamp(P.original / 100.0, 0.0, 1.0);
    return out8(mix(c, o, k), s.a);
}
