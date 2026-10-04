// After Effects "Exposure" (ADBE Exposure2). Measured against AE 2026 (8 bpc, no colour management), exact on every
// opaque input level (0 mismatches over the full 0..255 LUT in both reference settings):
//   c   = straight colour 0..1 (the layer is un-premultiplied first, alpha is untouched, re-premultiplied at the end)
//   lin = c ^ 2.4                       ("linear light conversion": a pure 2.4 power, NOT the piecewise sRGB curve)
//   lin = lin * 2^Exposure + Offset
//   lin = max(lin, 0) ^ (1 / Gamma Correction)
//   out = lin ^ (1 / 2.4)               (clamped 0..1 by the 8 bpc output)
// "Bypass Linear Light Conversion" skips both 2.4 power steps (the maths runs on the encoded values).
// Channels = Master (1) uses Exposure/Offset/Gamma for R, G and B; Individual Channels (2) uses the per-channel groups
// only (the Master values are ignored, as in AE's UI where they are greyed out).
struct Params {
    size: vec2<f32>,
    channels: f32,        // AE 1 Channels popup 1 = Master, 2 = Individual Channels
    exposure: f32,        // AE 3 Master Exposure, stops -100..100 (linear gain 2^x)
    offset: f32,          // AE 4 Master Offset -2..2 (added in linear light)
    gammaCorr: f32,       // AE 5 Master Gamma Correction 0.1..10 (out = x^(1/gamma))
    redExposure: f32,     // AE 8 Red Exposure, stops -100..100
    redOffset: f32,       // AE 9 Red Offset -2..2
    redGamma: f32,        // AE 10 Red Gamma Correction 0.1..10
    greenExposure: f32,   // AE 13 Green Exposure, stops -100..100
    greenOffset: f32,     // AE 14 Green Offset -2..2
    greenGamma: f32,      // AE 15 Green Gamma Correction 0.1..10
    blueExposure: f32,    // AE 18 Blue Exposure, stops -100..100
    blueOffset: f32,      // AE 19 Blue Offset -2..2
    blueGamma: f32,       // AE 20 Blue Gamma Correction 0.1..10
    bypassLinear: f32,    // AE 22 Bypass Linear Light Conversion checkbox 0/1
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

const LIN_GAMMA: f32 = 2.4;

// pow() with 0 -> 0 (WGSL leaves pow(0, y) to the backend)
fn spow(x: vec3<f32>, y: vec3<f32>) -> vec3<f32> {
    return select(pow(max(x, vec3<f32>(1e-30)), y), vec3<f32>(0.0), x <= vec3<f32>(0.0));
}

fn expose(c: vec3<f32>, e: vec3<f32>, o: vec3<f32>, g: vec3<f32>) -> vec3<f32> {
    let lin = select(spow(c, vec3<f32>(LIN_GAMMA)), c, P.bypassLinear > 0.5);
    let x = lin * exp2(e) + o;
    let y = spow(x, vec3<f32>(1.0) / max(g, vec3<f32>(1e-4)));
    let enc = select(spow(y, vec3<f32>(1.0 / LIN_GAMMA)), y, P.bypassLinear > 0.5);
    return clamp(enc, vec3<f32>(0.0), vec3<f32>(1.0));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureSample(srcTex, srcSamp, in.uv);          // premultiplied
    if (s.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    let c = clamp(s.rgb / s.a, vec3<f32>(0.0), vec3<f32>(1.0));
    var e = vec3<f32>(P.exposure);
    var o = vec3<f32>(P.offset);
    var g = vec3<f32>(P.gammaCorr);
    if (P.channels > 1.5) {
        e = vec3<f32>(P.redExposure, P.greenExposure, P.blueExposure);
        o = vec3<f32>(P.redOffset, P.greenOffset, P.blueOffset);
        g = vec3<f32>(P.redGamma, P.greenGamma, P.blueGamma);
    }
    return vec4<f32>(expose(c, e, o, g) * s.a, s.a);
}
