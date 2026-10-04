// After Effects "Channel Blur" (ADBE Channel Blur) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Each premultiplied channel is blurred with its own Blurriness (Red, Green, Blue, Alpha). Assumed to follow the
// measured legacy blur law (three iterated boxes of radius 0.369 x Blurriness), here as the Gaussian of the same
// variance. Blur Dimensions 1 Horizontal and Vertical, 2 Horizontal, 3 Vertical; Repeat Edge Pixels clamps
// coordinates, otherwise transparent outside. Pass 0 horizontal, pass 1 vertical.
struct Params {
    size: vec2<f32>,
    rB: f32,              // AE 1 Red Blurriness
    gB: f32,              // AE 2 Green Blurriness
    bB: f32,              // AE 3 Blue Blurriness
    aB: f32,              // AE 4 Alpha Blurriness
    repeatEdge: f32,      // AE 5 Edge Behavior: Repeat Edge Pixels
    dims: f32,            // AE 6 Blur Dimensions (menu)
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

const MAXR: i32 = 120;

fn sigmaOf(b: f32) -> f32 {
    let w = 2.0 * 0.369 * max(b, 0.0) + 1.0;
    return sqrt(max(3.0 * (w * w - 1.0) / 12.0, 1e-6));
}

fn fetch(p: vec2<i32>) -> vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    if (P.repeatEdge > 0.5) {
        return textureLoad(srcTex, clamp(p, vec2<i32>(0), dimi - vec2<i32>(1)), 0);
    }
    if (any(p < vec2<i32>(0)) || any(p >= dimi)) {
        return vec4<f32>(0.0);
    }
    return textureLoad(srcTex, p, 0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let horizontal = P.passIndex < 0.5;
    let dims = i32(round(P.dims));
    if ((horizontal && dims == 3) || (!horizontal && dims == 2)) {
        return textureLoad(srcTex, ip, 0);
    }
    let sig = vec4<f32>(sigmaOf(P.rB), sigmaOf(P.gB), sigmaOf(P.bB), sigmaOf(P.aB));
    let r = min(i32(ceil(3.0 * max(max(sig.r, sig.g), max(sig.b, sig.a)))), MAXR);
    let dir = select(vec2<i32>(0, 1), vec2<i32>(1, 0), horizontal);
    var acc = vec4<f32>(0.0);
    var ws = vec4<f32>(0.0);
    for (var i = -r; i <= r; i++) {
        let x = f32(i);
        let w = exp(-0.5 * x * x / (sig * sig));
        acc += w * fetch(ip + dir * i);
        ws += w;
    }
    let o = acc / ws;
    return round(vec4<f32>(min(o.rgb, vec3<f32>(1.0)), o.a) * 255.0) / 255.0;
}
