// After Effects "CC Color Neutralizer" (CC Color Neutralizer) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Removes a colour cast per tonal band: each Unbalance colour's departure from its own grey is subtracted from the
// pixels of that band (Shadows / Midtones / Highlights amounts in %), band weights from the luminance around Pivot
// (smooth ramps); Contrast (%) then scales around Pivot.
struct Params {
    size: vec2<f32>,
    shColor: vec4<f32>,   // AE 1 Shadows Unbalance
    midColor: vec4<f32>,  // AE 3 Midtones Unbalance
    hiColor: vec4<f32>,   // AE 5 Highlights Unbalance
    shadows: f32,         // AE 2 Shadows (%)
    midtones: f32,        // AE 4 Midtones (%)
    highlights: f32,      // AE 6 Highlights (%)
    pivot: f32,           // AE 7 Pivot (0..1)
    contrast: f32,        // AE 8 Contrast (%)
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
fn luma(c: vec3<f32>) -> f32 { return dot(c, vec3<f32>(0.299, 0.587, 0.114)); }

fn castOf(c: vec3<f32>) -> vec3<f32> {
    return c - vec3<f32>(luma(c));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let L = luma(c);
    let pv = clamp(P.pivot, 0.01, 0.99);
    let ws = 1.0 - smoothstep(0.0, pv, L);
    let wh = smoothstep(pv, 1.0, L);
    let wm = 1.0 - abs(L - pv) / max(pv, 1.0 - pv);
    var o = c - castOf(P.shColor.rgb) * ws * P.shadows / 100.0 - castOf(P.midColor.rgb) * wm * P.midtones / 100.0
              - castOf(P.hiColor.rgb) * wh * P.highlights / 100.0;
    o = vec3<f32>(pv) + (o - vec3<f32>(pv)) * (1.0 + P.contrast / 100.0);
    return out8(o, s.a);
}
