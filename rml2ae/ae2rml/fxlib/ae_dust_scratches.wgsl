// After Effects "Dust & Scratches" (ADBE Dust & Scratches) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Per channel, the median of the (2r+1)^2 square (r = Radius, at most 12) replaces the pixel where it differs from
// it by more than Threshold levels (0..255); elsewhere the pixel is kept. Premultiplied; alpha kept.
struct Params {
    size: vec2<f32>,
    radius: f32,          // AE 1 Radius (px)
    threshold: f32,       // AE 2 Threshold (0..255)
    passIndex: f32,
    pad0: f32,
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

const MAXR: i32 = 12;

fn fetch(p: vec2<i32>) -> vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    if (any(p < vec2<i32>(0)) || any(p >= dimi)) {
        return vec4<f32>(0.0);
    }
    return textureLoad(srcTex, p, 0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let r = clamp(i32(round(P.radius)), 0, MAXR);
    if (r == 0) {
        return s;
    }
    let n = f32((2 * r + 1) * (2 * r + 1));
    var lo = vec3<f32>(0.0);
    var hi = vec3<f32>(1.0);
    for (var it = 0; it < 12; it++) {
        let mid = 0.5 * (lo + hi);
        var below = vec3<f32>(0.0);
        for (var y = -r; y <= r; y++) {
            for (var x = -r; x <= r; x++) {
                below += select(vec3<f32>(0.0), vec3<f32>(1.0), fetch(ip + vec2<i32>(x, y)).rgb <= mid);
            }
        }
        let up = below >= vec3<f32>(0.5 * n);
        hi = select(hi, mid, up);
        lo = select(mid, lo, up);
    }
    let med = min(0.5 * (lo + hi), vec3<f32>(s.a));
    let far = abs(s.rgb - med) * 255.0 > vec3<f32>(P.threshold);
    let o = select(s.rgb, med, far);
    return round(vec4<f32>(o, s.a) * 255.0) / 255.0;
}
