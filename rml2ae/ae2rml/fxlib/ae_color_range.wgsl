// After Effects "Color Range" (ADBE Color Range) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Keys out the pixels whose colour, in Color Space 1 Lab, 2 YUV or 3 RGB (0..255 per component, a/b and U/V
// centred on 128), lies inside the box [Min, Max] of each component; Fuzziness (0..255) softens the box edges
// linearly. The eyedroppers (Key Color, Plus, Minus) only set Min / Max in AE and are not read.
struct Params {
    size: vec2<f32>,
    fuzz: f32,            // AE 5 Fuzziness
    space: f32,           // AE 6 Color Space (menu)
    min0: f32,            // AE 7 Min (L, Y, R)
    max0: f32,            // AE 8 Max (L, Y, R)
    min1: f32,            // AE 9 Min (a, U, G)
    max1: f32,            // AE 10 Max (a, U, G)
    min2: f32,            // AE 11 Min (b, V, B)
    max2: f32,            // AE 12 Max (b, V, B)
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

fn lin(c: vec3<f32>) -> vec3<f32> {
    return select(pow((c + 0.055) / 1.055, vec3<f32>(2.4)), c / 12.92, c <= vec3<f32>(0.04045));
}

fn labf(t: f32) -> f32 {
    return select(t / (3.0 * 0.0428061) + 4.0 / 29.0, pow(t, 1.0 / 3.0), t > 0.008856);
}

fn toSpace(c: vec3<f32>, sp: i32) -> vec3<f32> {
    if (sp == 3) {
        return c * 255.0;
    }
    if (sp == 2) {
        let y = dot(c, vec3<f32>(0.299, 0.587, 0.114));
        return vec3<f32>(y * 255.0, (0.492 * (c.b - y)) * 255.0 + 128.0, (0.877 * (c.r - y)) * 255.0 + 128.0);
    }
    let l = lin(c);
    let X = dot(l, vec3<f32>(0.4124, 0.3576, 0.1805)) / 0.95047;
    let Y = dot(l, vec3<f32>(0.2126, 0.7152, 0.0722));
    let Z = dot(l, vec3<f32>(0.0193, 0.1192, 0.9505)) / 1.08883;
    let fx = labf(X);
    let fy = labf(Y);
    let fz = labf(Z);
    return vec3<f32>((116.0 * fy - 16.0) * 2.55, 500.0 * (fx - fy) + 128.0, 200.0 * (fy - fz) + 128.0);
}

fn inside(v: f32, lo: f32, hi: f32, fz: f32) -> f32 {
    let d = max(lo - v, v - hi);
    return 1.0 - clamp(d / max(fz, 1e-4) + select(0.0, 1.0, fz <= 0.0 && d > 0.0), 0.0, 1.0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let v = toSpace(c, i32(round(P.space)));
    let k = inside(v.x, P.min0, P.max0, P.fuzz) * inside(v.y, P.min1, P.max1, P.fuzz) * inside(v.z, P.min2, P.max2, P.fuzz);
    return out8(c, s.a * (1.0 - k));
}
