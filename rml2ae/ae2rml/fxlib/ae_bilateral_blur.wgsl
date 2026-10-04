// After Effects "Bilateral Blur" (ADBE Bilateral Blur) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Edge-preserving blur: each pixel averages its (2r+1)^2 neighbourhood (r = Radius, at most 16) with spatial weights
// exp(-d^2 / (2 (r/2)^2)) and range weights exp(-|dc|^2 / (2 t^2)), t = Threshold / 100 (straight colour); Colorize
// is not modelled (the colour is kept). Alpha is averaged with the spatial weights only.
struct Params {
    size: vec2<f32>,
    radius: f32,          // AE 1 Radius (px)
    threshold: f32,       // AE 2 Threshold (%)
    colorize: f32,        // AE 3 Colorize (not modelled)
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

const MAXR: i32 = 16;

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let c0 = straight8(s);
    let r = clamp(i32(round(P.radius)), 0, MAXR);
    if (r == 0) {
        return s;
    }
    let ss = max(f32(r) * 0.5, 0.5);
    let tr = max(P.threshold / 100.0, 1e-3);
    var acc = vec3<f32>(0.0);
    var wc = 0.0;
    var aa = 0.0;
    var wa = 0.0;
    for (var y = -r; y <= r; y++) {
        for (var x = -r; x <= r; x++) {
            let q = ip + vec2<i32>(x, y);
            if (any(q < vec2<i32>(0)) || any(q >= dimi)) {
                continue;
            }
            let t = textureLoad(srcTex, q, 0);
            let ws = exp(-0.5 * f32(x * x + y * y) / (ss * ss));
            aa += ws * t.a;
            wa += ws;
            if (t.a > 0.0) {
                let c = straight8(t);
                let d = c - c0;
                let w = ws * exp(-0.5 * dot(d, d) / (tr * tr)) * t.a;
                acc += w * c;
                wc += w;
            }
        }
    }
    var c = c0;
    if (wc > 0.0) {
        c = acc / wc;
    }
    return out8(c, s.a);
}
