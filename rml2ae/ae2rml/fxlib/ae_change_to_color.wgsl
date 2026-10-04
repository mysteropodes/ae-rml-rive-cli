// After Effects "Change to Color" (ADBE Change To Color) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Pixels whose hue, lightness and saturation lie within Tolerance (Hue, Lightness, Saturation %) of From are changed
// to To: Change 1 Hue, 2 Hue & Lightness, 3 Hue & Saturation, 4 Hue, Lightness & Saturation; Change By 1 Setting To
// Color (the components take To's values) or 2 Transforming To Color (they move by To - From). Softness % widens
// each tolerance into a linear ramp. View Correction Matte shows the weight in grey.
struct Params {
    size: vec2<f32>,
    fromC: vec4<f32>,     // AE 1 From
    toC: vec4<f32>,       // AE 2 To
    change: f32,          // AE 3 Change (menu)
    changeBy: f32,        // AE 4 Change By (menu)
    tolH: f32,            // AE 6 Tolerance: Hue (%)
    tolL: f32,            // AE 7 Tolerance: Lightness (%)
    tolS: f32,            // AE 8 Tolerance: Saturation (%)
    softness: f32,        // AE 9 Softness (%)
    matte: f32,           // AE 10 View Correction Matte
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

fn within(d: f32, tol: f32, soft: f32) -> f32 {
    return 1.0 - clamp((d - tol) / max(soft, 1e-4), 0.0, 1.0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let x = rgb2hls(c);
    let f = rgb2hls(P.fromC.rgb);
    let t = rgb2hls(P.toC.rgb);
    let dh0 = abs(x.x - f.x);
    let dh = min(dh0, 1.0 - dh0) * 2.0;
    let soft = P.softness / 100.0 * 0.5;
    let w = within(dh, P.tolH / 100.0, soft) * within(abs(x.y - f.y), P.tolL / 100.0, soft)
          * within(abs(x.z - f.z), P.tolS / 100.0, soft);
    if (P.matte > 0.5) {
        return out8(vec3<f32>(w), s.a);
    }
    let mode = i32(round(P.change));
    let useL = mode == 2 || mode == 4;
    let useS = mode == 3 || mode == 4;
    var y = x;
    if (i32(round(P.changeBy)) == 2) {
        y.x = fract(x.x + t.x - f.x + 1.0);
        if (useL) {
            y.y = x.y + t.y - f.y;
        }
        if (useS) {
            y.z = x.z + t.z - f.z;
        }
    } else {
        y.x = t.x;
        if (useL) {
            y.y = t.y;
        }
        if (useS) {
            y.z = t.z;
        }
    }
    return out8(mix(c, hls2rgb(y), w), s.a);
}
