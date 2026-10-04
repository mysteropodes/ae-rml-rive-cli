// After Effects "CC Vector Blur" (CC Vector Blur) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The layer's own Property (1 Red, 2 Green, 3 Blue, 4 Alpha, 5 Luminance, 6 Lightness, 7 Hue, 8 Saturation) is the
// vector map: its gradient (central differences over 1 + Map Softness / 10 px) gives the direction. Type 1 Natural
// blurs along the contours (perpendicular to the gradient) over Amount x |gradient| px, 2 Constant Length over Amount
// px, 3 Perpendicular across the contours, 4 Direction Center and 5 Direction Fading along the gradient (5 fading the
// far taps). Angle Offset turns the direction. Ridge Smoothness and an external Vector Map are not modelled.
struct Params {
    size: vec2<f32>,
    kind: f32,            // AE 1 Type (menu)
    amount: f32,          // AE 2 Amount (px)
    angleOffset: f32,     // AE 3 Angle Offset (degrees)
    ridge: f32,           // AE 4 Ridge Smoothness
    property: f32,        // AE 6 Property (menu)
    softness: f32,        // AE 7 Map Softness
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

fn rgb2hls(c: vec3<f32>) -> vec3<f32> {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let l = 0.5 * (mx + mn);
    let d = mx - mn;
    if (d <= 1e-6) {
        return vec3<f32>(0.0, l, 0.0);
    }
    var s = d / (mx + mn);
    if (l > 0.5) {
        s = d / (2.0 - mx - mn);
    }
    var h: f32;
    if (mx == c.r) {
        h = (c.g - c.b) / d;
    } else if (mx == c.g) {
        h = 2.0 + (c.b - c.r) / d;
    } else {
        h = 4.0 + (c.r - c.g) / d;
    }
    return vec3<f32>(fract(h / 6.0 + 1.0), l, s);
}

fn hue2c(p: f32, q: f32, t0: f32) -> f32 {
    let t = fract(t0 + 1.0);
    if (t < 1.0 / 6.0) {
        return p + (q - p) * 6.0 * t;
    }
    if (t < 0.5) {
        return q;
    }
    if (t < 2.0 / 3.0) {
        return p + (q - p) * (2.0 / 3.0 - t) * 6.0;
    }
    return p;
}

fn hls2rgb(x: vec3<f32>) -> vec3<f32> {
    let h = x.x;
    let l = clamp(x.y, 0.0, 1.0);
    let s = clamp(x.z, 0.0, 1.0);
    if (s <= 0.0) {
        return vec3<f32>(l);
    }
    var q = l + s - l * s;
    if (l < 0.5) {
        q = l * (1.0 + s);
    }
    let p = 2.0 * l - q;
    return vec3<f32>(hue2c(p, q, h + 1.0 / 3.0), hue2c(p, q, h), hue2c(p, q, h - 1.0 / 3.0));
}

fn prop(q: vec2<f32>) -> f32 {
    let t = tapL(q);
    let c = straight8(t);
    let m = i32(round(P.property));
    if (m == 1) {
        return c.r;
    }
    if (m == 2) {
        return c.g;
    }
    if (m == 3) {
        return c.b;
    }
    if (m == 4) {
        return t.a;
    }
    if (m == 5) {
        return dot(c, vec3<f32>(0.299, 0.587, 0.114));
    }
    let x = rgb2hls(c);
    if (m == 7) {
        return x.x;
    }
    if (m == 8) {
        return x.z;
    }
    return x.y;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let p = layerPos(in.uv);
    let h = 1.0 + max(P.softness, 0.0) / 10.0;
    var g = vec2<f32>(prop(p + vec2<f32>(h, 0.0)) - prop(p - vec2<f32>(h, 0.0)),
                      prop(p + vec2<f32>(0.0, h)) - prop(p - vec2<f32>(0.0, h))) / (2.0 * h);
    let gl = length(g);
    let kind = i32(round(P.kind));
    var dir = select(vec2<f32>(1.0, 0.0), g / gl, gl > 1e-6);
    if (kind == 1 || kind == 2) {
        dir = vec2<f32>(-dir.y, dir.x);
    }
    let a = radians(P.angleOffset);
    dir = vec2<f32>(dir.x * cos(a) - dir.y * sin(a), dir.x * sin(a) + dir.y * cos(a));
    var len = P.amount;
    if (kind == 1) {
        len = P.amount * min(gl * 10.0, 1.0);
    }
    if (len < 0.25) {
        return tapL(p);
    }
    let n = 24;
    var acc = vec4<f32>(0.0);
    var ws = 0.0;
    for (var i = 0; i < n; i++) {
        let u = (f32(i) + 0.5) / f32(n) - 0.5;
        var w = 1.0;
        if (kind == 5) {
            w = 1.0 - abs(u) * 2.0;
        }
        acc += w * tapL(p + dir * u * len);
        ws += w;
    }
    return round(acc / ws * 255.0) / 255.0;
}
