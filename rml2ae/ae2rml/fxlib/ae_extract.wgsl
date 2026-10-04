// After Effects "Extract" (ADBE Extract) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Keeps the pixels whose Channel value (1 Luminance, 2 Red, 3 Green, 4 Blue, 5 Alpha; 0..255) lies in
// [Black Point, White Point], with linear ramps of Black / White Softness levels outside; Invert swaps kept and
// removed. The layer's alpha is multiplied by that matte. Parameter 1 is the histogram (no value).
struct Params {
    size: vec2<f32>,
    channel: f32,         // AE 2 Channel (menu)
    blackPt: f32,         // AE 3 Black Point (0..255)
    whitePt: f32,         // AE 4 White Point
    blackSoft: f32,       // AE 5 Black Softness
    whiteSoft: f32,       // AE 6 White Softness
    invert: f32,          // AE 7 Invert
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let ch = i32(round(P.channel));
    var v = dot(c, vec3<f32>(0.299, 0.587, 0.114));
    if (ch == 2) {
        v = c.r;
    } else if (ch == 3) {
        v = c.g;
    } else if (ch == 4) {
        v = c.b;
    } else if (ch == 5) {
        v = s.a;
    }
    let x = v * 255.0;
    let lo = clamp((x - P.blackPt) / max(P.blackSoft, 1e-3) + 1.0, 0.0, 1.0);
    let hi = clamp((P.whitePt - x) / max(P.whiteSoft, 1e-3) + 1.0, 0.0, 1.0);
    var m = min(lo, hi);
    if (P.invert > 0.5) {
        m = 1.0 - m;
    }
    return out8(c, s.a * m);
}
