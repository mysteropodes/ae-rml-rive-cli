// After Effects "CC Glass Wipe" (CC Glass Wipe) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Reveals Layer to Reveal (the second layer) through a glassy edge: where this layer's luminance is below Completion
// (Softness % ramp) the revealed layer shows; in the transition band both are displaced along the luminance slope by
// Displacement Amount px. The Gradient Layer is taken as this layer itself (a third layer is not bound).
struct Params {
    size: vec2<f32>,
    completion: f32,      // AE 1 Completion (%)
    softness: f32,        // AE 4 Softness (%)
    amount: f32,          // AE 5 Displacement Amount
    passIndex: f32,
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

// layer px of the centre of this fragment's canvas pixel
fn layerPos(uv: vec2<f32>) -> vec2<f32> {
    return vec2<f32>(pixelOf(uv)) + vec2<f32>(0.5) - P.layerRect.xy;
}

fn layerSize() -> vec2<f32> {
    return max(P.layerRect.zw - P.layerRect.xy, vec2<f32>(1.0));
}

// bilinear sample of the layer at layer px q (pixel i centre = i + 0.5), premultiplied, transparent outside the layer
fn tapL(q: vec2<f32>) -> vec4<f32> {
    let size = layerSize();
    let p = q - vec2<f32>(0.5);
    let o = max(-p, p - (size - vec2<f32>(1.0)));
    let cov = clamp(vec2<f32>(1.0) - o, vec2<f32>(0.0), vec2<f32>(1.0));
    let c = cov.x * cov.y;
    let dims = vec2<f32>(textureDimensions(srcTex, 0));
    if (c <= 0.0) {
        return vec4<f32>(0.0);
    }
    return c * textureSampleLevel(srcTex, srcSamp, (q + P.layerRect.xy) / dims, 0.0);
}

// a point parameter (the host writes it in canvas px, i.e. layer px + fxPad) -> layer px
fn layerPt(c: vec2<f32>) -> vec2<f32> {
    return c - P.layerRect.xy;
}

// AE angle convention: 0 = up, clockwise (y down)
fn aeDir(deg: f32) -> vec2<f32> {
    let a = radians(deg);
    return vec2<f32>(sin(a), -cos(a));
}
@group(0) @binding(3) var mapTex: texture_2d<f32>;

fn mapAt(q: vec2<f32>) -> vec4<f32> {
    let sz = layerSize();
    if (q.x < 0.0 || q.y < 0.0 || q.x >= sz.x || q.y >= sz.y) {
        return vec4<f32>(0.0);
    }
    let md = vec2<i32>(textureDimensions(mapTex, 0));
    let mp = clamp(vec2<i32>(floor(q * vec2<f32>(md) / sz)), vec2<i32>(0), md - vec2<i32>(1));
    return textureLoad(mapTex, mp, 0);
}
fn luma(c: vec3<f32>) -> f32 { return dot(c, vec3<f32>(0.299, 0.587, 0.114)); }

fn g1(q: vec2<f32>) -> f32 {
    let t = tapL(q);
    return luma(straight8(t)) * t.a;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let g = g1(q);
    let w = max(P.softness / 100.0, 1e-3);
    let t = clamp(P.completion / 100.0, 0.0, 1.0) * (1.0 + w);
    let k = clamp((t - g) / w, 0.0, 1.0);                   // 1: revealed
    let band = 1.0 - abs(k * 2.0 - 1.0);
    let grad = vec2<f32>(g1(q + vec2<f32>(2.0, 0.0)) - g1(q - vec2<f32>(2.0, 0.0)), g1(q + vec2<f32>(0.0, 2.0)) - g1(q - vec2<f32>(0.0, 2.0)));
    let d = grad * P.amount * band;
    let a = tapL(q + d);
    let b = mapAt(q - d);
    return round(mix(a, b, k) * 255.0) / 255.0;
}
