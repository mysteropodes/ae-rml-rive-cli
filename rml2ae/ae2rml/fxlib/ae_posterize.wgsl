// After Effects "Posterize" (ADBE Posterize, FR "Postérisation") — measured on AE 2026 8 bpc. AE works on the
// STRAIGHT 8-bit colour of each pixel, alpha untouched, then premultiplies:
//   idx = min(floor(c8 * Level / 255), Level - 1)  (/255, not /256: L=6 held-out ref, 51/255 off with /256), c8' = round(idx * 255 / (Level - 1)).
// Bit-exact vs AE at Level 4 and 2 (64 -> 85, 128 -> 170, 192 -> 255; the 50 % alpha region keeps its alpha and
// is posterized on its straight colour). For Levels where 255 / (Level - 1) is not an integer the half-way
// rounding (round half up here) is not covered by a visible reference.
struct Params {
    size: vec2<f32>,
    level: f32,           // AE 1 Level 2..255
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    let ip = clamp(vec2<i32>(floor(in.pos.xy)), vec2<i32>(0), dimi - vec2<i32>(1));
    let keep = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0).a * 0.0;   // keeps binding 1 in the auto layout
    let s = textureLoad(srcTex, ip, 0) + vec4<f32>(keep);                 // premultiplied
    if (s.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    let a8 = round(s.a * 255.0);
    let c8 = clamp(round(s.rgb / s.a * 255.0), vec3<f32>(0.0), vec3<f32>(255.0));   // AE's straight 8-bit colour
    let lv = clamp(round(P.level), 2.0, 255.0);
    let idx = min(floor(c8 * lv / 255.0), vec3<f32>(lv - 1.0));
    let o8 = floor(idx * 255.0 / (lv - 1.0) + 0.5);
    let pm = floor((o8 * a8 + 127.0) / 255.0);                            // AE 8-bit premultiply
    return vec4<f32>(pm / 255.0, a8 / 255.0);
}
