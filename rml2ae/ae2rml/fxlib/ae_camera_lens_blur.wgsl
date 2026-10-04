// After Effects "Camera Lens Blur" (ADBE Camera Lens Blur) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A lens (bokeh) blur: a uniform kernel over the iris, a regular polygon of Shape sides (assumed menu 1 Triangle .. 8
// Decagon) of radius Blur Radius px, rounded towards a disc by Roundness %, stretched by Aspect Ratio and turned by
// Rotation; 96 taps. Highlights above Threshold (0..255) are boosted by Gain before averaging. Diffraction Fringe, the
// Blur Map layer, noise and Repeat Edge Pixels are not modelled. Positions assumed (groups counted).
struct Params {
    size: vec2<f32>,
    radius: f32,          // AE 1 Blur Radius (px)
    shape: f32,           // AE 3 Iris Shape (menu)
    roundness: f32,       // AE 4 Iris Roundness (%)
    aspect: f32,          // AE 5 Iris Aspect Ratio
    rotation: f32,        // AE 6 Iris Rotation (degrees)
    gain: f32,            // AE 14 Highlight Gain
    threshold: f32,       // AE 15 Highlight Threshold (0..255)
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

// radius of the polygon of n sides (circumradius 1) in direction a, blended with the circle by k
fn iris(a: f32, n: f32, k: f32) -> f32 {
    let seg = 6.28318531 / n;
    let x = a - seg * floor(a / seg) - 0.5 * seg;
    let poly = cos(0.5 * seg) / cos(x);
    return mix(poly, 1.0, k);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let r = max(P.radius, 0.0);
    if (r < 0.5) {
        return tapL(q);
    }
    let n = f32(i32(round(P.shape)) + 2);
    let k = clamp(P.roundness / 100.0, 0.0, 1.0);
    let rot = radians(P.rotation);
    let asp = max(P.aspect, 0.01);
    let thr = P.threshold / 255.0;
    var acc = vec4<f32>(0.0);
    let N = 96;
    for (var i = 0; i < N; i++) {
        let t = (f32(i) + 0.5) / f32(N);
        let a = f32(i) * 2.39996323;
        let rr = sqrt(t) * iris(a, n, k);
        var o = vec2<f32>(cos(a + rot), sin(a + rot)) * rr * r;
        o.x = o.x * asp;
        var s = tapL(q + o);
        let l = max(s.r, max(s.g, s.b));
        if (l > thr && P.gain > 0.0) {
            s = vec4<f32>(s.rgb * (1.0 + P.gain / 10.0 * (l - thr) / max(1.0 - thr, 1e-3)), s.a);
        }
        acc += s;
    }
    let o = acc / f32(N);
    return round(vec4<f32>(min(o.rgb, vec3<f32>(o.a)), o.a) * 255.0) / 255.0;
}
