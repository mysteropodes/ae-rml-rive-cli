// After Effects "Find Edges" (ADBE Find Edges) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Sobel gradient per channel on the straight colour, g = clamp(|grad| / 4); the result is 1 - g (dark edges on white)
// or g with Invert. Blend With Original mixes with the source. Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    invert: f32,          // AE 1 Invert (checkbox)
    blend: f32,           // AE 2 Blend With Original (%)
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

fn px(p: vec2<i32>) -> vec3<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    return straight8(textureLoad(srcTex, clamp(p, vec2<i32>(0), dimi - vec2<i32>(1)), 0));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let a = px(ip + vec2<i32>(-1, -1));
    let b = px(ip + vec2<i32>(0, -1));
    let c = px(ip + vec2<i32>(1, -1));
    let d = px(ip + vec2<i32>(-1, 0));
    let f = px(ip + vec2<i32>(1, 0));
    let g = px(ip + vec2<i32>(-1, 1));
    let h = px(ip + vec2<i32>(0, 1));
    let k = px(ip + vec2<i32>(1, 1));
    let gx = (c + 2.0 * f + k) - (a + 2.0 * d + g);
    let gy = (g + 2.0 * h + k) - (a + 2.0 * b + c);
    let e = clamp(sqrt(gx * gx + gy * gy) / 4.0, vec3<f32>(0.0), vec3<f32>(1.0));
    var o = vec3<f32>(1.0) - e;
    if (P.invert > 0.5) {
        o = e;
    }
    return out8(mix(o, straight8(s), clamp(P.blend, 0.0, 1.0)), s.a);
}
