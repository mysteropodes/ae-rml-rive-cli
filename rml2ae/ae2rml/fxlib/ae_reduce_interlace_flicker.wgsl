// After Effects "Reduce Interlace Flicker" (ADBE Reduce Interlace Flicker) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A vertical Gaussian blur of sigma = Softness px (premultiplied, transparent outside the layer).
struct Params {
    size: vec2<f32>,
    softness: f32,        // AE 1 Softness (px)
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

const MAXR: i32 = 64;

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    let ip = pixelOf(in.uv);
    let sig = max(P.softness, 0.0);
    if (sig <= 0.0) {
        return textureLoad(srcTex, ip, 0);
    }
    let r = min(i32(ceil(3.0 * sig)), MAXR);
    var acc = vec4<f32>(0.0);
    var ws = 0.0;
    for (var i = -r; i <= r; i++) {
        let w = exp(-0.5 * f32(i * i) / (sig * sig));
        let q = ip + vec2<i32>(0, i);
        if (q.y >= 0 && q.y < dimi.y) {
            acc += w * textureLoad(srcTex, q, 0);
        }
        ws += w;
    }
    return round(acc / ws * 255.0) / 255.0;
}
