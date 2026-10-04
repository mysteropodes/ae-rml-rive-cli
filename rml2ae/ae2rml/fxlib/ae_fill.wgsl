// After Effects "Fill" (ADBE Fill) — every pixel takes the fill colour, alpha kept; Opacity scales the layer's alpha
// (measured: Opacity 0.5 -> premultiplied (128, 64, 0, 128) for an orange fill, the original does not show through).
// Masks (Fill Mask / All Masks / Invert / feathers) are not reproduced: the effect applies to the whole layer.
struct Params {
    size: vec2<f32>,
    pad0: vec2<f32>,
    color: vec4<f32>,     // AE 3 Color (straight 0..1)
    opacity: f32,         // AE 7 Opacity 0..1
    passIndex: f32,
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureSample(srcTex, srcSamp, in.uv);          // premultiplied
    let a = s.a * clamp(P.opacity, 0.0, 1.0);
    return vec4<f32>(P.color.rgb * a, a);
}
