// After Effects "Optics Compensation" (ADBE Optics Compensation) — radial lens (barrel) distortion around Center.
// Measured against AE 2026 8 bpc (fxref optics_compensation_0/1):
//   * F = L / tan(FOV / 2), L = half the layer WIDTH (FOV Orientation 1 Horizontal; 2 Vertical = half height,
//     3 Diagonal = half diagonal — those two are the natural reading, not measured);
//   * normal: output pixel at radius r from Center samples the layer at radius s = r / sqrt(1 - (r/F)^2)
//     (= F tan(asin(r/F))): the image shrinks towards the edges; r >= F is transparent;
//   * Reverse Lens Distortion: s = r / sqrt(1 + (r/F)^2) (= F sin(atan(r/F)), the exact inverse) — bit-exact;
//   * pixel centres at +0.5 (layer px), bilinear on the premultiplied layer, transparent outside the layer.
// Residual: where the map minifies by more than ~1.3x (layer edges far from Center) AE filters slightly differently
// (edge ramps differ by up to ~40 levels on ~0.2 % of the pixels); bilinear kept because it is exact everywhere else.
// Not reproduced: AE 5 Optimal Pixels, AE 6 Resize Layer (the canvas is the layer). The reference length uses the
// canvas size, so keep the Rive node's fxPad at 0.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,     // AE 4 Center (point, layer px, default = layer centre)
    fov: f32,              // AE 1 Field Of View (FOV) degrees 0..180 (default 0 = no distortion)
    reverse: f32,          // AE 2 Reverse Lens Distortion checkbox 0/1
    orientation: f32,      // AE 3 FOV Orientation popup 1 Horizontal, 2 Vertical, 3 Diagonal
    optimal: f32,          // AE 5 Optimal Pixels (Invalidates Reverse) checkbox (not reproduced)
    resize: f32,           // AE 6 Resize Layer popup (not reproduced)
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

// bilinear sample at pixel-index coordinates q (pixel i centre = i), transparent outside the layer
fn tap(q: vec2<f32>, size: vec2<f32>) -> vec4<f32> {
    let o = max(-q, q - (size - vec2<f32>(1.0)));
    let cov = clamp(vec2<f32>(1.0) - o, vec2<f32>(0.0), vec2<f32>(1.0));
    let c = cov.x * cov.y;
    var v = vec4<f32>(0.0);
    if (c > 0.0) {
        v = c * textureSampleLevel(srcTex, srcSamp, (q + vec2<f32>(0.5)) / size, 0.0);
    }
    return v;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let size = vec2<f32>(textureDimensions(srcTex));
    let pc = floor(in.uv * size) + vec2<f32>(0.5);          // output pixel centre, layer px
    let fov = clamp(P.fov, 0.0, 179.99);
    if (fov <= 0.0) {
        return tap(pc - vec2<f32>(0.5), size);
    }
    let o = i32(round(P.orientation));
    var L = 0.5 * size.x;
    if (o == 2) {
        L = 0.5 * size.y;
    } else if (o == 3) {
        L = 0.5 * length(size);
    }
    let F = L / tan(radians(fov) * 0.5);
    let d = pc - P.center;
    let r = length(d) / F;
    var k = 1.0;
    if (P.reverse > 0.5) {
        k = inverseSqrt(1.0 + r * r);
    } else {
        if (r >= 1.0) {
            return vec4<f32>(0.0);
        }
        k = inverseSqrt(1.0 - r * r);
    }
    return tap(P.center + d * k - vec2<f32>(0.5), size);
}
