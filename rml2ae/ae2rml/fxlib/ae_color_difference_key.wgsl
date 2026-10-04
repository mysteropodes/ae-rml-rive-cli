// After Effects "Color Difference Key" (ADBE Color Difference Key) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Two partial mattes from the colour difference to Key Color: A = distance to the key (0..1, RGB), B = distance to
// the key's complement; each through its In Black / In White / Gamma levels (0..255) to Out Black / Out White; the
// matte = max(A, B) through Matte In Black / In White / Gamma. View 1 Final Output, 2 Source, 3 Matte (assumed
// menu). Color Matching Accuracy is not modelled. Parameter positions assumed.
struct Params {
    size: vec2<f32>,
    key: vec4<f32>,       // AE 2 Key Color
    view: f32,            // AE 1 View (menu)
    aInB: f32,            // AE 4 Partial A In Black
    aInW: f32,            // AE 5 Partial A In White
    aGam: f32,            // AE 6 Partial A Gamma
    aOutB: f32,           // AE 7 Partial A Out Black
    aOutW: f32,           // AE 8 Partial A Out White
    bInB: f32,            // AE 9 Partial B In Black
    bInW: f32,            // AE 10 Partial B In White
    bGam: f32,            // AE 11 Partial B Gamma
    bOutB: f32,           // AE 12 Partial B Out Black
    bOutW: f32,           // AE 13 Partial B Out White
    mInB: f32,            // AE 14 Matte In Black
    mInW: f32,            // AE 15 Matte In White
    mGam: f32,            // AE 16 Matte Gamma
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

fn lev(v: f32, ib: f32, iw: f32, g: f32, ob: f32, ow: f32) -> f32 {
    let t = clamp((v * 255.0 - ib) / max(iw - ib, 1e-3), 0.0, 1.0);
    return (ob + (ow - ob) * pow(t, 1.0 / max(g, 1e-3))) / 255.0;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let view = i32(round(P.view));
    if (view == 2) {
        return s;
    }
    let c = straight8(s);
    let da = length(c - P.key.rgb) / sqrt(3.0);
    let db = 1.0 - length(c - (vec3<f32>(1.0) - P.key.rgb)) / sqrt(3.0);
    let a = lev(da, P.aInB, P.aInW, P.aGam, P.aOutB, P.aOutW);
    let b = lev(db, P.bInB, P.bInW, P.bGam, P.bOutB, P.bOutW);
    let m = lev(max(a, b), P.mInB, P.mInW, P.mGam, 0.0, 255.0);
    if (view == 3) {
        return out8(vec3<f32>(m * s.a), 1.0);
    }
    return out8(c, s.a * m);
}
