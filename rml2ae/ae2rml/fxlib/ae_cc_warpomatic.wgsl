// After Effects "CC WarpoMatic" (CC WarpoMatic) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A warping transition to Layer to Reveal (the second layer): the layer's Driver (assumed menu 1 Brightness, 2 Contrast
// Differential, 3 Brightness Differential, 4 Local Differences; all taken as brightness here) decides when each pixel
// switches, over Blend Span %; around the switch both images are pushed along the brightness slope by Warp Amount px
// (Warp Direction 1 Joint, 2 Opposing, 3 Twisting). Reactor (a third layer) and Smoothness are not modelled.
struct Params {
    size: vec2<f32>,
    completion: f32,      // AE 1 Completion (%)
    driver: f32,          // AE 4 Driver (menu)
    smoothness: f32,      // AE 5 Smoothness
    amount: f32,          // AE 6 Warp Amount
    direction: f32,       // AE 7 Warp Direction (menu)
    span: f32,            // AE 8 Blend Span (%)
    passIndex: f32,
    pad0: f32,
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

// the second layer at layer px q: the generated Rive node renders it at the layer's size, so it is stretched over the
// layer (AE "If Layer Sizes Differ" has nothing left to decide); transparent outside the layer
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

fn br(q: vec2<f32>) -> f32 {
    let t = tapL(q);
    return luma(straight8(t)) * t.a;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let g = br(q);
    let w = max(P.span / 100.0, 1e-3);
    let k = clamp((clamp(P.completion / 100.0, 0.0, 1.0) * (1.0 + w) - g) / w, 0.0, 1.0);
    let band = 1.0 - abs(k * 2.0 - 1.0);
    var d = vec2<f32>(br(q + vec2<f32>(2.0, 0.0)) - br(q - vec2<f32>(2.0, 0.0)), br(q + vec2<f32>(0.0, 2.0)) - br(q - vec2<f32>(0.0, 2.0)));
    let dir = i32(round(P.direction));
    if (dir == 3) {
        d = vec2<f32>(-d.y, d.x);
    }
    let off = d * P.amount * band;
    var offB = off;
    if (dir == 2) {
        offB = -off;
    }
    let a = tapL(q + off);
    let b = mapAt(q + offB);
    return round(mix(a, b, k) * 255.0) / 255.0;
}
