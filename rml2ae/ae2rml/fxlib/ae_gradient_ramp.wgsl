// After Effects "Gradient Ramp" (ADBE Ramp, FR "Gamme des degrades") — replaces the layer colours by a linear or
// radial ramp between two points; the layer's alpha is kept. Measured against AE 2026 8 bpc (pixel-exact):
//   * pixel (i, j) is evaluated at the integer point (i, j) (pixel top-left), not at the pixel centre;
//   * Linear: t = projection of p on start->end, clamped 0..1 (lines perpendicular to start->end are iso-colour);
//     Radial: t = |p - start| / |end - start|, clamped (end point only sets the radius);
//   * colour = lerp(start, end, t) per channel in the working (sRGB) space, quantised to 8 bits STRAIGHT, then
//     premultiplied by the layer alpha (half-alpha pixels match AE to the unit only with this order).
// Blend With Original mixes the result with the untouched (premultiplied) layer. Ramp Scatter jitters the ramp
// position per pixel (AE's dither noise is proprietary: structure approximated, see gradient_ramp.json notes).
struct Params {
    size: vec2<f32>,
    startPoint: vec2<f32>,  // AE 1 Start of Ramp (point, layer px)
    startColor: vec4<f32>,  // AE 2 Start Color (straight color 0..1, alpha ignored)
    endColor: vec4<f32>,    // AE 4 End Color (straight color 0..1, alpha ignored)
    endPoint: vec2<f32>,    // AE 3 End of Ramp (point, layer px)
    rampShape: f32,         // AE 5 Ramp Shape popup 1 = Linear Ramp, 2 = Radial Ramp
    rampScatter: f32,       // AE 6 Ramp Scatter 0..512 (px of jitter along the ramp)
    blend: f32,             // AE 7 Blend With Original 0..1 (0 = ramp only)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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

// integer hash (PCG) -> uniform 0..1, stable per pixel
fn hash01(ix: u32, iy: u32) -> f32 {
    var v = ix * 1973u + iy * 9277u + 26699u;
    v = v * 747796405u + 2891336453u;
    let w = ((v >> ((v >> 28u) + 4u)) ^ v) * 277803737u;
    return f32((w >> 22u) ^ w) / 4294967295.0;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureSample(srcTex, srcSamp, in.uv);          // premultiplied layer content
    let p = in.pos.xy - vec2<f32>(0.5);                      // AE evaluates the ramp at the pixel's integer corner
    let d = P.endPoint - P.startPoint;
    let len2 = max(dot(d, d), 1e-8);
    let len = sqrt(len2);
    // Ramp Scatter: per-pixel jitter of the ramp position, in px along the ramp (uniform in +-scatter/2)
    let jit = (hash01(u32(max(p.x, 0.0)), u32(max(p.y, 0.0))) - 0.5) * max(P.rampScatter, 0.0);
    var t: f32;
    if (P.rampShape > 1.5) {
        t = (length(p - P.startPoint) + jit) / len;
    } else {
        t = (dot(p - P.startPoint, d) / len + jit) / len;
    }
    t = clamp(t, 0.0, 1.0);
    let straight = mix(P.startColor.rgb, P.endColor.rgb, t);
    let q = floor(clamp(straight, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5) / 255.0;   // AE 8 bpc straight
    let ramp = vec4<f32>(q * s.a, s.a);
    return mix(ramp, s, clamp(P.blend, 0.0, 1.0));
}
