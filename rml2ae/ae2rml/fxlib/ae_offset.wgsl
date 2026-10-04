// After Effects "Offset" (ADBE Offset) — UNVERIFIED: written from the effect's definition, not yet measured against
// After Effects renders. The layer is scrolled so that its centre lands on "Shift Center To" and wraps around (tiles):
// out(p) = in((p - (shift - layer centre)) mod layer size), bilinear for fractional shifts (premultiplied).
// Blend With Original mixes with the untouched layer. Positions in layer pixels; the wrap uses P.layerRect.
struct Params {
    size: vec2<f32>,
    shift: vec2<f32>,     // AE 1 Shift Center To (layer px)
    blend: f32,           // AE 2 Blend With Original (%)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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

fn wrapTexel(q: vec2<i32>, org: vec2<i32>, wh: vec2<i32>) -> vec4<f32> {
    let m = ((q % wh) + wh) % wh;
    return textureLoad(srcTex, org + m, 0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let orig = textureLoad(srcTex, ip, 0);
    let org = vec2<i32>(round(P.layerRect.xy));
    let wh = max(vec2<i32>(round(P.layerRect.zw - P.layerRect.xy)), vec2<i32>(1));
    let p = vec2<f32>(ip - org) + vec2<f32>(0.5);                 // pixel centre, layer px
    if (p.x < 0.0 || p.y < 0.0 || p.x > f32(wh.x) || p.y > f32(wh.y)) {
        return vec4<f32>(0.0);
    }
    let d = P.shift - 0.5 * vec2<f32>(wh);
    let q = p - d - vec2<f32>(0.5);                               // source position in texel space
    let q0 = vec2<i32>(floor(q));
    let f = q - floor(q);
    let a = mix(wrapTexel(q0, org, wh), wrapTexel(q0 + vec2<i32>(1, 0), org, wh), f.x);
    let b = mix(wrapTexel(q0 + vec2<i32>(0, 1), org, wh), wrapTexel(q0 + vec2<i32>(1, 1), org, wh), f.x);
    let o = mix(mix(a, b, f.y), orig, clamp(P.blend / 100.0, 0.0, 1.0));
    return round(o * 255.0) / 255.0;
}
