// After Effects "Unsharp Mask" (ADBE Unsharp Mask2) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// c' = c + Amount/100 * (c - blur(c)) where |c - blur(c)| > Threshold (raw 0..1); blur = Gaussian of sigma = Radius px
// (two separable passes on the premultiplied layer, transparent outside). Pass 0: horizontal blur; pass 1: vertical
// blur + sharpen against origTex. Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    amount: f32,          // AE 2 Amount (%)
    radius: f32,          // AE 3 Radius (px)
    threshold: f32,       // AE 4 Threshold (raw 0..1)
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

@group(0) @binding(3) var origTex: texture_2d<f32>;

const MAXR: i32 = 96;

fn blur1(ip: vec2<i32>, dir: vec2<i32>) -> vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    let sigma = max(P.radius, 0.3);
    let r = min(i32(ceil(sigma * 3.0)), MAXR);
    var acc = vec4<f32>(0.0);
    var wsum = 0.0;
    for (var i = -r; i <= r; i++) {
        let w = exp(-0.5 * f32(i * i) / (sigma * sigma));
        let q = ip + dir * i;
        var v = vec4<f32>(0.0);
        if (all(q >= vec2<i32>(0)) && all(q < dimi)) {
            v = textureLoad(srcTex, q, 0);
        }
        acc += w * v;
        wsum += w;
    }
    return acc / wsum;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    if (P.passIndex < 0.5) {
        return blur1(ip, vec2<i32>(1, 0));
    }
    let bl = blur1(ip, vec2<i32>(0, 1));
    let o = textureLoad(origTex, ip, 0);
    let c = straight8(o);
    var b = vec3<f32>(0.0);
    if (bl.a > 0.0) {
        b = clamp(bl.rgb / bl.a, vec3<f32>(0.0), vec3<f32>(1.0));
    }
    let diff = c - b;
    let on = select(vec3<f32>(0.0), vec3<f32>(1.0), abs(diff) > vec3<f32>(P.threshold));
    return out8(c + (P.amount / 100.0) * diff * on, o.a);
}
