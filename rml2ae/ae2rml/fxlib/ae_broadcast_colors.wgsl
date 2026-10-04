// After Effects "Broadcast Colors" (ADBE Broadcast Colors) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Composite video amplitude Y + |chroma| (YIQ, NTSC; PAL uses YUV) in IRE (0..1 maps to 7.5..100 IRE for NTSC, 0..100
// for PAL) is limited to Maximum Signal: How To Make Color Safe 1 Reduce Luminance (scales the colour), 2 Reduce
// Saturation (moves it towards its luma), 3 Key Out Unsafe, 4 Key Out Safe (alpha).
struct Params {
    size: vec2<f32>,
    locale: f32,          // AE 1 Broadcast Locale: 1 NTSC, 2 PAL
    method: f32,          // AE 2 How To Make Color Safe (menu)
    maxIre: f32,          // AE 3 Maximum Signal Amplitude (IRE)
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

fn amplitude(c: vec3<f32>, pal: bool) -> f32 {
    let y = dot(c, vec3<f32>(0.299, 0.587, 0.114));
    var ch: f32;
    if (pal) {
        ch = length(vec2<f32>(0.492 * (c.b - y), 0.877 * (c.r - y)));
        return (y + ch) * 100.0;
    }
    ch = length(vec2<f32>(dot(c, vec3<f32>(0.596, -0.274, -0.322)), dot(c, vec3<f32>(0.211, -0.523, 0.312))));
    return 7.5 + (y + ch) * 92.5;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let pal = i32(round(P.locale)) == 2;
    let amp = amplitude(c, pal);
    let notSafe = amp > P.maxIre;
    let m = i32(round(P.method));
    if (m == 3) {
        return out8(c, select(s.a, 0.0, notSafe));
    }
    if (m == 4) {
        return out8(c, select(0.0, s.a, notSafe));
    }
    if (!notSafe) {
        return s;
    }
    var o = c;
    if (m == 2) {
        let y = vec3<f32>(dot(c, vec3<f32>(0.299, 0.587, 0.114)));
        var lo = 0.0;
        var hi = 1.0;
        for (var i = 0; i < 16; i++) {
            let k = 0.5 * (lo + hi);
            if (amplitude(mix(y, c, k), pal) > P.maxIre) {
                hi = k;
            } else {
                lo = k;
            }
        }
        o = mix(y, c, lo);
    } else {
        var lo = 0.0;
        var hi = 1.0;
        for (var i = 0; i < 16; i++) {
            let k = 0.5 * (lo + hi);
            if (amplitude(c * k, pal) > P.maxIre) {
                hi = k;
            } else {
                lo = k;
            }
        }
        o = c * lo;
    }
    return out8(o, s.a);
}
