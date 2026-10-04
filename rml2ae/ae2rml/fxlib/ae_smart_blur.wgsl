// After Effects "Smart Blur" (ADBE Smart Blur) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Each pixel averages the neighbours within Radius (at most 16) whose straight colour differs from it by less than
// Threshold (0..255, max channel difference). Mode 1 Normal; 2 Edge Only (white edges on black where the blur was
// cut); 3 Overlay Edge (those edges drawn white over the result). Quality is ignored.
struct Params {
    size: vec2<f32>,
    radius: f32,          // AE 1 Radius (px)
    threshold: f32,       // AE 2 Threshold (0..255)
    quality: f32,         // AE 3 Quality (not modelled)
    mode: f32,            // AE 4 Mode (menu)
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

const MAXR: i32 = 16;

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let c0 = straight8(s);
    let r = clamp(i32(round(P.radius)), 0, MAXR);
    var acc = vec4<f32>(0.0);
    var n = 0.0;
    var cut = 0.0;
    var tot = 0.0;
    for (var y = -r; y <= r; y++) {
        for (var x = -r; x <= r; x++) {
            if (x * x + y * y > r * r) {
                continue;
            }
            let q = ip + vec2<i32>(x, y);
            if (any(q < vec2<i32>(0)) || any(q >= dimi)) {
                continue;
            }
            let t = textureLoad(srcTex, q, 0);
            let d = abs(straight8(t) - c0) * 255.0;
            tot += 1.0;
            if (max(d.r, max(d.g, d.b)) <= P.threshold) {
                acc += t;
                n += 1.0;
            } else {
                cut += 1.0;
            }
        }
    }
    var o = s;
    if (n > 0.0) {
        o = acc / n;
    }
    let edge = select(0.0, 1.0, cut > 0.25 * tot);
    let mode = i32(round(P.mode));
    if (mode == 2) {
        return vec4<f32>(vec3<f32>(edge), 1.0) * s.a;
    }
    if (mode == 3) {
        o = mix(o, vec4<f32>(o.a), edge);
    }
    return round(o * 255.0) / 255.0;
}
