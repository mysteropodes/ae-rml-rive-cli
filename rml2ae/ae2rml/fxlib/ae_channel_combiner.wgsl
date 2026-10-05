// After Effects "Channel Combiner" (ADBE Channel Combiner) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// From (assumed menu order): 1 RGB to HLS, 2 HLS to RGB, 3 RGB to YUV, 4 YUV to RGB, 5 Lightness, 6 Hue,
// 7 Saturation, 8 Red, 9 Green, 10 Blue, 11 Alpha, 12 Min RGB, 13 Max RGB. The four conversions rewrite the colour;
// the single values go To: 1 Red Only, 2 Green Only, 3 Blue Only, 4 Alpha Only, 5 Lightness, 6 Hue, 7 Saturation.
// Invert inverts the result; Solid Alpha makes the layer opaque. Use 2nd Layer / Source Layer (3) are not modelled (the layer
// itself). AE's menus (19 / 17 entries, To default 7) contain separators: both orders are still to be measured.
struct Params {
    size: vec2<f32>,
    use2nd: f32,          // AE 2 Use 2nd Layer
    fromMode: f32,        // AE 5 From (menu, 19 entries in AE)
    toMode: f32,          // AE 6 To (menu, 17 entries in AE)
    invert: f32,          // AE 7 Invert
    solid: f32,           // AE 8 Solid Alpha
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

const YUVM = mat3x3<f32>(vec3<f32>(0.299, -0.14713, 0.615), vec3<f32>(0.587, -0.28886, -0.51499),
                         vec3<f32>(0.114, 0.436, -0.10001));
const RGBM = mat3x3<f32>(vec3<f32>(1.0, 1.0, 1.0), vec3<f32>(0.0, -0.39465, 2.03211),
                         vec3<f32>(1.13983, -0.5806, 0.0));

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    var o = c;
    var a = s.a;
    let fm = i32(round(P.fromMode));
    if (fm == 1) {
        o = rgb2hls(c);
    } else if (fm == 2) {
        o = hls2rgb(c);
    } else if (fm == 3) {
        let y = YUVM * c;
        o = vec3<f32>(y.x, y.y + 0.5, y.z + 0.5);
    } else if (fm == 4) {
        o = RGBM * vec3<f32>(c.x, c.y - 0.5, c.z - 0.5);
    } else {
        let x = rgb2hls(c);
        var v = x.y;
        if (fm == 6) {
            v = x.x;
        } else if (fm == 7) {
            v = x.z;
        } else if (fm == 8) {
            v = c.r;
        } else if (fm == 9) {
            v = c.g;
        } else if (fm == 10) {
            v = c.b;
        } else if (fm == 11) {
            v = s.a;
        } else if (fm == 12) {
            v = min(c.r, min(c.g, c.b));
        } else if (fm == 13) {
            v = max(c.r, max(c.g, c.b));
        }
        if (P.invert > 0.5) {
            v = 1.0 - v;
        }
        let tm = i32(round(P.toMode));
        if (tm == 1) {
            o.r = v;
        } else if (tm == 2) {
            o.g = v;
        } else if (tm == 3) {
            o.b = v;
        } else if (tm == 4) {
            a = v;
        } else {
            var y = x;
            if (tm == 5) {
                y.y = v;
            } else if (tm == 6) {
                y.x = v;
            } else {
                y.z = v;
            }
            o = hls2rgb(y);
        }
        if (P.solid > 0.5) {
            a = 1.0;
        }
        return out8(o, a);
    }
    if (P.invert > 0.5) {
        o = vec3<f32>(1.0) - o;
    }
    if (P.solid > 0.5) {
        a = 1.0;
    }
    return out8(o, a);
}
