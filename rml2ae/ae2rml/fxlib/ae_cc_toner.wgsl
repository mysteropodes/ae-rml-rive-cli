// After Effects "CC Toner" (CC Toner) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The Rec.601 luma of the straight colour is mapped through a gradient of the chosen colours: Tones 1 Duotone
// (Shadows -> Highlights), 2 Tritone (Shadows -> Midtones -> Highlights), 3 Pentone (Shadows, Darktones, Midtones,
// Brights, Highlights), 4 Solid (Midtones only); then Blend w. Original. Alpha untouched; premultiplied.
struct Params {
    size: vec2<f32>,
    tones: f32,           // AE 1 Tones (menu)
    blend: f32,           // AE 7 Blend w. Original (%)
    highlights: vec4<f32>, // AE 2 Highlights
    brights: vec4<f32>,   // AE 3 Brights
    midtones: vec4<f32>,  // AE 4 Midtones
    darktones: vec4<f32>, // AE 5 Darktones
    shadows: vec4<f32>,   // AE 6 Shadows
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
    let y = dot(c, vec3<f32>(0.299, 0.587, 0.114));
    let t = i32(round(P.tones));
    var o: vec3<f32>;
    if (t == 1) {
        o = mix(P.shadows.rgb, P.highlights.rgb, y);
    } else if (t == 3) {
        let x = y * 4.0;
        if (x < 1.0) {
            o = mix(P.shadows.rgb, P.darktones.rgb, x);
        } else if (x < 2.0) {
            o = mix(P.darktones.rgb, P.midtones.rgb, x - 1.0);
        } else if (x < 3.0) {
            o = mix(P.midtones.rgb, P.brights.rgb, x - 2.0);
        } else {
            o = mix(P.brights.rgb, P.highlights.rgb, x - 3.0);
        }
    } else if (t == 4) {
        o = P.midtones.rgb;
    } else {
        if (y < 0.5) {
            o = mix(P.shadows.rgb, P.midtones.rgb, y * 2.0);
        } else {
            o = mix(P.midtones.rgb, P.highlights.rgb, y * 2.0 - 1.0);
        }
    }
    return out8(mix(o, c, clamp(P.blend / 100.0, 0.0, 1.0)), s.a);
}
