// After Effects "CC Scale Wipe" (Cycore, AE 2026) — measured against AE 8 bpc (Stretch 40 / Direction 50 and
// Stretch -30 / Direction 120, Center default), pixel-exact (max 1 level):
//   * smear direction d = (sin θ, -cos θ) (θ = Direction, degrees clockwise from up), reversed when Stretch < 0;
//   * the half-plane behind the line through Center perpendicular to d is untouched; at signed distance u > 0 past
//     the line (along d) a pixel takes the source at distance g(u) = u / (1 + u |Stretch| / R) along the same
//     perpendicular offset v, R = half the layer diagonal (sqrt(w² + h²) / 2: fitted L·|Stretch| = 367.155 on the
//     640x360 refs, half diagonal = 367.151, the same for both settings). g saturates at R / |Stretch| px: the
//     content just past the line is stretched to infinity;
//   * Center is a pixel CENTRE (AE point (x, y) = pixel-corner coordinates (x + 0.5, y + 0.5)): with the corner
//     convention the -30/120 setting needs a 0.68 px shift, with the centre one both settings fit with no offset;
//   * the source is read with bilinear interpolation of the premultiplied pixels, transparent outside the layer.
// Stretch 0 = identity. R = half the LAYER diagonal (P.layerRect, filled by the host): right with fxPad > 0 too.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 2 Center (layer px, default = layer centre)
    stretch: f32,         // AE 1 Stretch -100..100 (default 0)
    direction: f32,       // AE 3 Direction degrees (default 50)
    passIndex: f32,
    pad0: f32,
    layerRect: vec4<f32>, // reserved, filled by the host: the layer's rect in the canvas (x0, y0, x1, y1)
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

fn texel(q: vec2<i32>, dims: vec2<i32>) -> vec4<f32> {
    if (q.x < 0 || q.y < 0 || q.x >= dims.x || q.y >= dims.y) {
        return vec4<f32>(0.0);
    }
    return textureLoad(srcTex, q, 0);
}

// bilinear sample at pixel-corner coordinates x (texel i centred at i + 0.5), transparent outside
fn bilinear(x: vec2<f32>, dims: vec2<i32>) -> vec4<f32> {
    let t = x - vec2<f32>(0.5);
    let f0 = floor(t);
    let a = t - f0;
    let i = vec2<i32>(f0);
    let c00 = texel(i, dims);
    let c10 = texel(i + vec2<i32>(1, 0), dims);
    let c01 = texel(i + vec2<i32>(0, 1), dims);
    let c11 = texel(i + vec2<i32>(1, 1), dims);
    return mix(mix(c00, c10, a.x), mix(c01, c11, a.x), a.y);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let px = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0), dims - vec2<i32>(1));
    let s = clamp(P.stretch, -100.0, 100.0);
    if (abs(s) < 1e-6) {
        return textureLoad(srcTex, px, 0);
    }
    let th = radians(P.direction);
    let d = vec2<f32>(sin(th), -cos(th)) * sign(s);
    let c = P.center + vec2<f32>(0.5);
    let x = vec2<f32>(px) + vec2<f32>(0.5);
    let u = dot(x - c, d);
    if (u <= 0.0) {
        return textureLoad(srcTex, px, 0);
    }
    let R = 0.5 * length(P.layerRect.zw - P.layerRect.xy);   // the layer's half diagonal (not the padded canvas)
    let g = u / (1.0 + u * abs(s) / R);
    return bilinear(x - (u - g) * d, dims);
}
