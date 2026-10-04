// After Effects "Key Cleaner" (match name assumed ADBE KeyCleaner) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Cleans a keyed matte: the alpha is averaged over Additional Edge Radius px (restoring detail lost at the edge),
// then its contrast is raised by Alpha Contrast % around 0.5; Strength % mixes the cleaned alpha with the original.
// Reduce Chatter needs neighbouring frames and is not modelled. Positions assumed.
struct Params {
    size: vec2<f32>,
    radius: f32,          // AE 1 Additional Edge Radius (px)
    chatter: f32,         // AE 2 Reduce Chatter
    contrast: f32,        // AE 3 Alpha Contrast (%)
    strength: f32,        // AE 4 Strength (%)
    passIndex: f32,
    pad0: f32,
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

fn aAt(p: vec2<i32>, dim: vec2<i32>) -> f32 {
    if (p.x < 0 || p.y < 0 || p.x >= dim.x || p.y >= dim.y) {
        return 0.0;
    }
    return textureLoad(srcTex, p, 0).a;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dim = vec2<i32>(textureDimensions(srcTex, 0));
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let r = max(P.radius, 0.0);
    var a = s.a;
    if (r >= 0.5) {
        var acc = 0.0;
        for (var i = 0; i < 24; i++) {
            let t = (f32(i) + 0.5) / 24.0;
            let ang = f32(i) * 2.39996323;
            acc += aAt(ip + vec2<i32>(round(vec2<f32>(cos(ang), sin(ang)) * sqrt(t) * r)), dim);
        }
        a = max(s.a, acc / 24.0);
    }
    a = clamp((a - 0.5) * (1.0 + max(P.contrast, 0.0) / 100.0) + 0.5, 0.0, 1.0);
    let o = mix(s.a, a, clamp(P.strength / 100.0, 0.0, 1.0));
    return out8(straight8(s), o);
}
