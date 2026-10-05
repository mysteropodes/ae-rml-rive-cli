// After Effects "Gamma/Pedestal/Gain" (ADBE Gamma/Pedestal/Gain) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Per channel on the straight colour: v = Pedestal + (Gain - Pedestal) * c^Gamma, no Blend With Original in AE (param 11 is the
// Compositing Options group). Black
// Stretch (1..4) lifts the low values of every channel before that: c' = 1 - (1 - c)^BlackStretch (a guess, to be
// measured; 1 = identity). Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    blackStretch: f32,    // AE 1 Black Stretch (1..4)
    rGamma: f32,          // AE 2 Red Gamma
    rPedestal: f32,       // AE 3 Red Pedestal
    rGain: f32,           // AE 4 Red Gain
    gGamma: f32,          // AE 5 Green Gamma
    gPedestal: f32,       // AE 6 Green Pedestal
    gGain: f32,           // AE 7 Green Gain
    bGamma: f32,          // AE 8 Blue Gamma
    bPedestal: f32,       // AE 9 Blue Pedestal
    bGain: f32,           // AE 10 Blue Gain
    pad0: f32,
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let cs = vec3<f32>(1.0) - pow(vec3<f32>(1.0) - c, vec3<f32>(max(P.blackStretch, 1.0)));
    let gam = max(vec3<f32>(P.rGamma, P.gGamma, P.bGamma), vec3<f32>(0.001));
    let ped = vec3<f32>(P.rPedestal, P.gPedestal, P.bPedestal);
    let gain = vec3<f32>(P.rGain, P.gGain, P.bGain);
    let v = ped + (gain - ped) * pow(cs, gam);
    return out8(v, s.a);
}
