// After Effects "Tint" (ADBE Tint) — maps the layer's luma onto a 2-colour ramp, then mixes with the original.
// Measured vs AE 2026 (8 bpc, bit-exact on the visible refs): AE works on STRAIGHT 8-bit colour;
// luma L8 = round((0.299 R + 0.587 G + 0.114 B) * 255) computed in float on 0..1 values (rounded to 8 bits BEFORE
// the ramp: an unrounded luma is off by 1 on ~20 % of the pixels); the two colours are quantised to 8 bits with
// round-half-up (0.1 -> 26/255, 0.9 -> 230/255, 0.3 -> 77/255); tinted = round(black + (white - black) * L8 / 255);
// alpha is kept. Amount to Tint (0..100 %) lerps original -> tinted (straight, then premultiplied by alpha).
// AE 4 "Swap Colors" is a button (no stored value, no effect on the image); AE 5 = Compositing Options (not reproduced).
struct Params {
    size: vec2<f32>,
    pad0: vec2<f32>,
    mapBlack: vec4<f32>,  // AE 1 Map Black To color (straight 0..1, alpha ignored)
    mapWhite: vec4<f32>,  // AE 2 Map White To color (straight 0..1, alpha ignored)
    amount: f32,          // AE 3 Amount to Tint 0..100 %
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

fn q8(c: vec3<f32>) -> vec3<f32> {
    return floor(clamp(c, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5) / 255.0;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    // exact texel fetch (canvas = layer size, no filtering)
    let dims = vec2<i32>(textureDimensions(srcTex));
    let ip = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0), dims - vec2<i32>(1));
    let keep = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0).a * 0.0;   // keeps binding 1 in the auto layout
    let s = textureLoad(srcTex, ip, 0) + vec4<f32>(keep);  // premultiplied
    if (s.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    let c = q8(s.rgb / s.a);                               // AE's straight 8-bit colour
    let lum = c.r * 0.299 + c.g * 0.587 + c.b * 0.114;
    let l8 = floor(lum * 255.0 + 0.5) / 255.0;
    let b = q8(P.mapBlack.rgb);
    let w = q8(P.mapWhite.rgb);
    let tinted = q8(b + (w - b) * l8);
    let k = clamp(P.amount * 0.01, 0.0, 1.0);
    let rgb = q8(mix(c, tinted, k));
    return vec4<f32>(rgb * s.a, s.a);
}
