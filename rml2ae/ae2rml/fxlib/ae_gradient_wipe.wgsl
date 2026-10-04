// After Effects "Gradient Wipe" (ADBE Gradient Wipe) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The layer itself is the gradient (Gradient Layer = the layer; another layer is not supported here): pixels whose
// Rec.601 luma is below the moving threshold disappear first. With c = Completion, s = Softness (0..1):
// matte = clamp((L - c * (1 + s)) / s + 1) (a hard step when s = 0); Invert Gradient uses 1 - L. Gradient Placement
// is ignored (same size).
struct Params {
    size: vec2<f32>,
    completion: f32,      // AE 1 Transition Completion (%)
    softness: f32,        // AE 2 Transition Softness (%)
    placement: f32,       // AE 4 Gradient Placement (not modelled)
    invert: f32,          // AE 5 Invert Gradient
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
    var L = dot(c, vec3<f32>(0.299, 0.587, 0.114));
    if (P.invert > 0.5) {
        L = 1.0 - L;
    }
    let comp = clamp(P.completion / 100.0, 0.0, 1.0);
    let soft = clamp(P.softness / 100.0, 0.0, 1.0);
    var m: f32;
    if (soft <= 0.0) {
        m = select(0.0, 1.0, L >= comp);
    } else {
        m = clamp((L - comp * (1.0 + soft)) / soft + 1.0, 0.0, 1.0);
    }
    if (comp <= 0.0) {
        m = 1.0;
    }
    return out8(c, s.a * m);
}
