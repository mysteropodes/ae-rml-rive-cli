// After Effects "Fractal" (ADBE Fractal) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The Mandelbrot / Julia set: Set Type (assumed menu) 1 Mandelbrot, 2 Mandelbrot Inverse, 3 Julia, 4 Julia Inverse,
// z <- z^2 + c (Equation 1 only). The view centre is Mandelbrot X / Y (Julia: Julia X / Y with the Mandelbrot point as
// c), its width 4 / 2^Magnification over the layer width; Escape Limit iterations. Colour: smooth iteration count
// through a hue cycle of Cycle Steps, shifted by Hue and Cycle Offset; the inside is black. Overlay, Transparency,
// Edge Highlight, the other palettes and High Quality settings are not modelled. Positions assumed (groups counted).
struct Params {
    size: vec2<f32>,
    setType: f32,         // AE 1 Set Type (menu)
    mx: f32,              // AE 4 Mandelbrot X (Real)
    my: f32,              // AE 5 Mandelbrot Y (Imaginary)
    mag: f32,             // AE 6 Mandelbrot Magnification
    escape: f32,          // AE 7 Mandelbrot Escape Limit
    jx: f32,              // AE 9 Julia X (Real)
    jy: f32,              // AE 10 Julia Y (Imaginary)
    jmag: f32,            // AE 11 Julia Magnification
    hue: f32,             // AE 20 Hue (degrees)
    steps: f32,           // AE 21 Cycle Steps
    cycleOffset: f32,     // AE 22 Cycle Offset (degrees)
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let sz = layerSize();
    let st = i32(round(P.setType));
    let julia = st >= 3;
    let magv = select(P.mag, P.jmag, julia);
    let w = 4.0 / exp2(magv);
    let ctr = select(vec2<f32>(P.mx, P.my), vec2<f32>(P.jx, P.jy), julia);
    var p = ctr + (q - 0.5 * sz) / sz.x * w;
    p.y = -p.y;
    if (st == 2 || st == 4) {
        p = p / max(dot(p, p), 1e-9);                        // inverse sets: z -> 1 / z
    }
    var z = select(vec2<f32>(0.0), p, julia);
    let c = select(p, vec2<f32>(P.mx, P.my), julia);
    let lim = i32(clamp(P.escape, 1.0, 1000.0));
    var n = 0;
    for (var i = 0; i < 1000; i++) {
        if (i >= lim || dot(z, z) > 256.0) { break; }
        z = vec2<f32>(z.x * z.x - z.y * z.y, 2.0 * z.x * z.y) + c;
        n++;
    }
    if (n >= lim) {
        return out8(vec3<f32>(0.0), 1.0);
    }
    let sm = f32(n) + 1.0 - log2(max(log2(max(dot(z, z), 1.0001)) * 0.5, 1e-6));
    let t = fract(sm / max(P.steps, 1.0) + (P.hue + P.cycleOffset) / 360.0);
    return out8(hls2rgb(vec3<f32>(t, 0.5, 1.0)), 1.0);
}
