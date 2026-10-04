// After Effects "Matte Choker" (ADBE Matte Choker) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Two choke stages on the alpha: each blurs the matte (Gaussian, sigma = Geometric Softness / 2 px), then sets a
// threshold from Choke (-127..127: t = 0.5 + Choke / 254) with a ramp of Gray Level Softness % around it.
// Pass 0 = stage 1, pass 1 = stage 2 with the colour of the original. Iterations > 1 are not modelled.
struct Params {
    size: vec2<f32>,
    geo1: f32,            // AE 1 Geometric Softness 1 (px)
    choke1: f32,          // AE 2 Choke 1
    gray1: f32,           // AE 3 Gray Level Softness 1 (%)
    geo2: f32,            // AE 4 Geometric Softness 2 (px)
    choke2: f32,          // AE 5 Choke 2
    gray2: f32,           // AE 6 Gray Level Softness 2 (%)
    iterations: f32,      // AE 7 Iterations
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
@group(0) @binding(3) var origTex: texture_2d<f32>;

const MAXR: i32 = 20;

fn aAt(p: vec2<i32>, dim: vec2<i32>) -> f32 {
    if (p.x < 0 || p.y < 0 || p.x >= dim.x || p.y >= dim.y) {
        return 0.0;
    }
    return textureLoad(srcTex, p, 0).a;
}

fn stage(p: vec2<i32>, geo: f32, choke: f32, gray: f32) -> f32 {
    let dim = vec2<i32>(textureDimensions(srcTex, 0));
    let sig = max(geo, 0.0) * 0.5;
    var b = aAt(p, dim);
    if (sig > 0.05) {
        let r = min(i32(ceil(3.0 * sig)), MAXR);
        var acc = 0.0;
        var ws = 0.0;
        for (var y = -r; y <= r; y++) {
            for (var x = -r; x <= r; x++) {
                let w = exp(-0.5 * f32(x * x + y * y) / (sig * sig));
                acc += w * aAt(p + vec2<i32>(x, y), dim);
                ws += w;
            }
        }
        b = acc / ws;
    }
    let t = 0.5 + clamp(choke, -127.0, 127.0) / 254.0;
    let w = max(gray / 100.0, 1e-3);
    return clamp((b - t) / w + 0.5, 0.0, 1.0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let p = pixelOf(in.uv);
    if (P.passIndex < 0.5) {
        return vec4<f32>(0.0, 0.0, 0.0, stage(p, P.geo1, P.choke1, P.gray1));
    }
    let a = stage(p, P.geo2, P.choke2, P.gray2);
    return out8(straight8(textureLoad(origTex, p, 0)), a);
}
