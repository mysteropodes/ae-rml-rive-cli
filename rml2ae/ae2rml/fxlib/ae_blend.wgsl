// After Effects "Blend" (ADBE Blend) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Blends the layer with a second layer: Mode 1 Crossfade (the other layer, alpha included), 2 Color Only (the
// other layer's hue and saturation on this layer's lightness), 3 Tint Only (the same, only where this layer is
// coloured), 4 Darken Only (min), 5 Lighten Only (max). Blend With Original % mixes the result back with the layer.
struct Params {
    size: vec2<f32>,
    mode: f32,            // AE 2 Mode (menu)
    original: f32,        // AE 3 Blend With Original (%)
    sizes: f32,           // AE 4 If Layer Sizes Differ (1 Center, 2 Stretch to Fit)
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
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let o = mapAt(layerPos(in.uv));
    let c = straight8(s);
    let oc = straight8(o);
    let mode = i32(round(P.mode));
    let k = 1.0 - clamp(P.original / 100.0, 0.0, 1.0);
    if (mode == 1) {
        let r = mix(s, o, k);
        return round(r * 255.0) / 255.0;
    }
    var r = c;
    if (mode == 2 || mode == 3) {
        let x = rgb2hls(c);
        let y = rgb2hls(oc);
        r = hls2rgb(vec3<f32>(y.x, x.y, y.z));
        if (mode == 3) {
            r = mix(c, r, clamp(x.z * 4.0, 0.0, 1.0));
        }
    } else if (mode == 4) {
        r = min(c, select(vec3<f32>(1.0), oc, o.a > 0.0));
    } else if (mode == 5) {
        r = max(c, oc * o.a);
    }
    return out8(mix(c, r, k), s.a);
}
