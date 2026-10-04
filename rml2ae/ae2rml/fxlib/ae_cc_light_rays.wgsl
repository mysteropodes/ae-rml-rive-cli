// After Effects "CC Light Rays" (CC Light Rays) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Rays from Center: the layer zoomed towards Center over Radius (as a fraction of the distance, Radius / 100) with
// fading weights, multiplied by Intensity / 100 and added to the layer (Transfer Mode treated as Add; Color from
// Source on). Shape, Direction, Warp Softness and Allow Brightening are not modelled; adding the whole layer
// saturates bright areas, AE probably weights the rays by brightness (to measure).
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 2 Center (layer px)
    intensity: f32,       // AE 1 Intensity
    radius: f32,          // AE 3 Radius
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

// light streaks: the layer (or its brighter part) zoomed towards Center and added
fn streak(p: vec2<f32>, c: vec2<f32>, len: f32, fade: bool) -> vec4<f32> {
    let v = p - c;
    let r = length(v);
    let span = clamp(len, 0.0, 1.0);
    let n = clamp(i32(ceil(span * r * 0.5)), 1, 128);
    var acc = vec4<f32>(0.0);
    var ws = 0.0;
    for (var i = 0; i < n; i++) {
        let t = (f32(i) + 0.5) / f32(n);
        var w = 1.0;
        if (fade) {
            w = 1.0 - t;
        }
        acc += w * tapL(c + v * (1.0 - t * span));
        ws += w;
    }
    return acc / max(ws, 1e-6);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let p = layerPos(in.uv);
    let s = tapL(p);
    let r = streak(p, layerPt(P.center), P.radius / 100.0, true);
    let o = s + r * (P.intensity / 100.0);
    let a = clamp(max(s.a, r.a * P.intensity / 100.0), 0.0, 1.0);
    return round(vec4<f32>(min(o.rgb, vec3<f32>(a)), a) * 255.0) / 255.0;
}
