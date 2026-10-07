// After Effects "Cartoon" (match name assumed ADBE Cartoonify) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Fill: the luminance is smoothed (a Detail Radius disc average where neighbours differ by less than Detail Threshold),
// then quantised into Shading Steps with Shading Smoothness % of each step's edge ramped; the colour follows the new
// luminance. Edges: black lines where the luminance gradient exceeds Edge Threshold, Width px wide, Softness % soft,
// Opacity %. Render 1 Fill, 2 Edges, 3 Fill & Edges. Advanced options are not modelled; positions assumed.
struct Params {
    size: vec2<f32>,
    render: f32,          // AE 1 Render (menu)
    radius: f32,          // AE 2 Detail Radius (px)
    threshold: f32,       // AE 3 Detail Threshold
    steps: f32,           // AE 5 Shading Steps
    smoothness: f32,      // AE 6 Shading Smoothness (%)
    edgeThr: f32,         // AE 9 Edge Threshold
    edgeWidth: f32,       // AE 10 Edge Width
    edgeSoft: f32,        // AE 11 Edge Softness (%)
    edgeOpacity: f32,     // AE 12 Edge Opacity (%)
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
fn luma(c: vec3<f32>) -> f32 { return dot(c, vec3<f32>(0.299, 0.587, 0.114)); }

fn px(p: vec2<i32>) -> vec4<f32> {
    let dim = vec2<i32>(textureDimensions(srcTex, 0));
    return textureLoad(srcTex, clamp(p, vec2<i32>(0), dim - vec2<i32>(1)), 0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = px(ip);
    let c = straight8(s);
    let L0 = luma(c);
    // edge-preserving smoothing of the colour
    var acc = vec3<f32>(0.0);
    var n = 0.0;
    let r = max(P.radius, 0.0);
    for (var i = 0; i < 24; i++) {
        let t = (f32(i) + 0.5) / 24.0;
        let a = f32(i) * 2.39996323;
        let q = ip + vec2<i32>(round(vec2<f32>(cos(a), sin(a)) * sqrt(t) * r));
        let cq = straight8(px(q));
        if (abs(luma(cq) - L0) * 100.0 <= max(P.threshold, 0.0) + 1e-3) {
            acc += cq;
            n += 1.0;
        }
    }
    let sm = select(c, acc / n, n > 0.0);
    let L = luma(sm);
    let st = max(round(P.steps), 1.0);
    let x = L * st;
    let f = fract(x);
    let w = clamp(P.smoothness / 100.0, 0.0, 1.0) * 0.5;
    var q = floor(x) + smoothstep(0.5 - w, 0.5 + w, f);
    if (w <= 0.0) { q = floor(x + 0.5); }
    let L2 = clamp(q / st, 0.0, 1.0);
    let fill = sm * (L2 / max(L, 1e-4));
    // edges from the Sobel gradient of the luminance
    let ww = max(1, i32(round(P.edgeWidth)));
    let gx = luma(straight8(px(ip + vec2<i32>(ww, 0)))) - luma(straight8(px(ip - vec2<i32>(ww, 0))));
    let gy = luma(straight8(px(ip + vec2<i32>(0, ww)))) - luma(straight8(px(ip - vec2<i32>(0, ww))));
    let g = length(vec2<f32>(gx, gy)) * 0.5;
    let thr = max(P.edgeThr, 0.0) * 0.1;
    let soft = max(P.edgeSoft / 100.0 * thr, 1e-3);
    let e = clamp((g - thr) / soft + 0.5, 0.0, 1.0) * clamp(P.edgeOpacity / 100.0, 0.0, 1.0);
    let mode = i32(round(P.render));
    if (mode == 1) {
        return out8(fill, s.a);
    }
    if (mode == 2) {
        return out8(vec3<f32>(1.0 - e), s.a);
    }
    return out8(fill * (1.0 - e), s.a);
}
