// After Effects "CC Color Neutralizer" (CS Color Neutralizer) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Removes a colour cast per tonal band: each Unbalance colour's departure from its own grey is subtracted from the
// pixels of that band (band weights from the luminance: shadows below 0.5, highlights above, midtones peaking at 0.5),
// then the band's Red / Green / Blue sliders (-100..100) add +-50 % of that channel in the band. Black / White Point
// (%) stretch the result, Blend w. Original (%) mixes the original back. Pinning and View (2, 3) are not modelled.
// Model re-fitted on AE 26's real parameter list (the strengths are a guess).
struct Params {
    size: vec2<f32>,
    shColor: vec4<f32>,   // AE 1 Shadows Unbalance
    midColor: vec4<f32>,  // AE 7 Midtones Unbalance
    hiColor: vec4<f32>,   // AE 13 Highlights Unbalance
    shR: f32,             // AE 3 Red - Shadows (-100..100)
    shG: f32,             // AE 4 Green - Shadows
    shB: f32,             // AE 5 Blue - Shadows
    midR: f32,            // AE 9 Red - Midtones
    midG: f32,            // AE 10 Green - Midtones
    midB: f32,            // AE 11 Blue - Midtones
    hiR: f32,             // AE 15 Red - Highlights
    hiG: f32,             // AE 16 Green - Highlights
    hiB: f32,             // AE 17 Blue - Highlights
    pinning: f32,         // AE 19 Pinning (%, not modelled)
    blend: f32,           // AE 20 Blend w. Original (%)
    view: f32,            // AE 22 View (menu, 1 = result only)
    blackPt: f32,         // AE 23 Black Point (%)
    whitePt: f32,         // AE 24 White Point (%)
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
fn luma(c: vec3<f32>) -> f32 { return dot(c, vec3<f32>(0.299, 0.587, 0.114)); }

fn castOf(c: vec3<f32>) -> vec3<f32> {
    return c - vec3<f32>(luma(c));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let L = luma(c);
    let ws = 1.0 - smoothstep(0.0, 0.5, L);
    let wh = smoothstep(0.5, 1.0, L);
    let wm = 1.0 - abs(L - 0.5) * 2.0;
    var o = c - castOf(P.shColor.rgb) * ws - castOf(P.midColor.rgb) * wm - castOf(P.hiColor.rgb) * wh;
    o += (vec3<f32>(P.shR, P.shG, P.shB) * ws + vec3<f32>(P.midR, P.midG, P.midB) * wm
          + vec3<f32>(P.hiR, P.hiG, P.hiB) * wh) * 0.005;
    let bp = clamp(P.blackPt / 100.0, 0.0, 0.499);
    let wp = clamp(P.whitePt / 100.0, 0.501, 1.0);
    o = (o - vec3<f32>(bp)) / (wp - bp);
    o = mix(o, c, clamp(P.blend / 100.0, 0.0, 1.0));
    return out8(o, s.a);
}
