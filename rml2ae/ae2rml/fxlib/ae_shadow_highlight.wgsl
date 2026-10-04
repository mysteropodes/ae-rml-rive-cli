// After Effects "Shadow/Highlight" (ADBE Shadow/Highlight) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Brightens the shadows and darkens the highlights from the local luminance (a disc average of Shadow / Highlight
// Radius px, 32 taps): shadows weigh (1 - L / tonalWidth)^2, highlights ((L - 1 + tonalWidth) / tonalWidth)^2 with
// tonal widths in %; the luminance moves by Amount % of what is left, the colour follows, Color Correction % scales the
// saturation of the change. Auto Amounts takes Shadow 50 / Highlight 0. Midtone Contrast, clips, Temporal Smoothing and
// Scene Detect are not modelled. Positions assumed past 6 (More Options group).
struct Params {
    size: vec2<f32>,
    autoAmt: f32,         // AE 1 Auto Amounts
    shadow: f32,          // AE 2 Shadow Amount (%)
    highlight: f32,       // AE 3 Highlight Amount (%)
    shWidth: f32,         // AE 7 Shadow Tonal Width (%)
    shRadius: f32,        // AE 8 Shadow Radius (px)
    hiWidth: f32,         // AE 9 Highlight Tonal Width (%)
    hiRadius: f32,        // AE 10 Highlight Radius (px)
    colorCorr: f32,       // AE 11 Color Correction (%)
    original: f32,        // AE 15 Blend With Original (%)
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

fn localL(p: vec2<i32>, r: f32) -> f32 {
    let dim = vec2<i32>(textureDimensions(srcTex, 0));
    var acc = 0.0;
    var n = 0.0;
    for (var i = 0; i < 32; i++) {
        let t = (f32(i) + 0.5) / 32.0;
        let a = f32(i) * 2.39996323;
        let q = clamp(p + vec2<i32>(round(vec2<f32>(cos(a), sin(a)) * sqrt(t) * r)), vec2<i32>(0), dim - vec2<i32>(1));
        let s = textureLoad(srcTex, q, 0);
        acc += luma(straight8(s));
        n += 1.0;
    }
    return acc / n;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let c = straight8(s);
    var sa = P.shadow / 100.0;
    var ha = P.highlight / 100.0;
    if (P.autoAmt > 0.5) {
        sa = 0.5;
        ha = 0.0;
    }
    let ls = localL(ip, max(P.shRadius, 0.0));
    let lh = localL(ip, max(P.hiRadius, 0.0));
    let ws = max(P.shWidth / 100.0, 1e-3);
    let wh = max(P.hiWidth / 100.0, 1e-3);
    let ms = pow(clamp(1.0 - ls / ws, 0.0, 1.0), 2.0);
    let mh = pow(clamp((lh - 1.0 + wh) / wh, 0.0, 1.0), 2.0);
    let L = luma(c);
    let L2 = clamp(L + sa * ms * (1.0 - L) * 0.6 - ha * mh * L * 0.6, 0.0, 1.0);
    let ratio = L2 / max(L, 1e-4);
    var o = c * ratio;
    let grey = vec3<f32>(luma(o));
    o = mix(grey, o, 1.0 + (P.colorCorr / 100.0 - 0.2) * (ratio - 1.0));
    return out8(mix(o, c, clamp(P.original / 100.0, 0.0, 1.0)), s.a);
}
