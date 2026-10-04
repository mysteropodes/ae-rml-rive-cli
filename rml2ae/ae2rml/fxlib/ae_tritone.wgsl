// After Effects "Tritone" (ADBE Tritone, FR "Trichrome") — maps the layer's luma onto a 3-colour ramp
// (Shadows -> Midtones -> Highlights), then mixes with the original. Measured vs AE 2026 (8 bpc):
//   AE works on the STRAIGHT 8-bit colour; L = 0.299 R + 0.587 G + 0.114 B in float (NOT rounded to 8 bits, unlike
//   Tint); the three colours are used unquantised (float); out = L < 0.5 ? mix(Shadows, Midtones, 2L)
//   : mix(Midtones, Highlights, 2L - 1); one rounding (half up) at the end. Alpha untouched.
//   Blend With Original (0..100 %) lerps tritone -> original (natural order, no visible reference).
struct Params {
    size: vec2<f32>,
    pad0: vec2<f32>,
    highlights: vec4<f32>, // AE 1 Highlights colour (straight 0..1, alpha ignored)
    midtones: vec4<f32>,   // AE 2 Midtones colour
    shadows: vec4<f32>,    // AE 3 Shadows colour
    blend: f32,            // AE 4 Blend With Original 0..100 %
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
    let dims = vec2<i32>(textureDimensions(srcTex));
    let ip = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0), dims - vec2<i32>(1));
    let keep = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0).a * 0.0;   // keeps binding 1 in the auto layout
    let s = textureLoad(srcTex, ip, 0) + vec4<f32>(keep);  // premultiplied
    if (s.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    let c = q8(s.rgb / s.a);                               // AE's straight 8-bit colour
    let l = dot(c, vec3<f32>(0.299, 0.587, 0.114));
    var t: vec3<f32>;
    if (l < 0.5) {
        t = mix(P.shadows.rgb, P.midtones.rgb, 2.0 * l);
    } else {
        t = mix(P.midtones.rgb, P.highlights.rgb, 2.0 * l - 1.0);
    }
    let k = clamp(P.blend * 0.01, 0.0, 1.0);
    let rgb = q8(mix(t, c, k));
    return vec4<f32>(rgb * s.a, s.a);
}
