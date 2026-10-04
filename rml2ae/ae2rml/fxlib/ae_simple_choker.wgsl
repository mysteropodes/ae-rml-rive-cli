// After Effects "Simple Choker" (ADBE Simple Choker) — chokes (Choke Matte > 0) or spreads (< 0) the layer's alpha.
// Measured model (AE 2026, 8 bpc, fitted on hard 0/128/255 alpha edges, corners and a 128-alpha region):
//   1. B = Gaussian blur of the alpha, sigma^2 = 0.635 * |choke| px^2 (sigma grows with sqrt(choke): AE's choke
//      "gets less effective the wider you go"); outside the layer = transparent.
//   2. levels on B around a FIXED level T = 7/8 (choke) or 1/8 (spread), ramp width W = 3 / (4 |choke|):
//      lo = max(0, T - W/2), hi = min(1, T + W/2), alpha' = clamp((B - lo) / (hi - lo)).  (choke 3 -> [0.75, 1],
//      choke -4 -> [0.031, 0.219]; the clamps make choke 0 the identity.)
//   3. colour = un-premultiplied original colour * alpha' (pixels that were transparent come out black).
// Two separable passes: pass 0 = horizontal blur of the alpha, stored as 16-bit fixed point packed in R (high byte)
// and G (low byte) because Rive keeps intermediate passes in RGBA8; pass 1 = vertical blur + levels + colour.
struct Params {
    size: vec2<f32>,
    view: f32,            // AE 1 View (popup: 1 = Final Output, 2 = Matte)
    choke: f32,           // AE 2 Choke Matte -100..100 (> 0 chokes, < 0 spreads)
    passIndex: f32,
    pad1: f32,
    pad2: f32,
    pad3: f32,
};
@group(0) @binding(0) var srcTex: texture_2d<f32>;
@group(0) @binding(1) var srcSamp: sampler;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var origTex: texture_2d<f32>;

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

const MAX_R: i32 = 40;

fn sigmaOf() -> f32 {
    return sqrt(0.635 * abs(P.choke));
}

fn radiusOf(sig: f32) -> i32 {
    return min(MAX_R, i32(ceil(sig * 3.5)));
}

// pass 0: alpha of the content (premultiplied texture), transparent outside
fn alphaAt(p: vec2<i32>, dim: vec2<i32>) -> f32 {
    if (p.x < 0 || p.y < 0 || p.x >= dim.x || p.y >= dim.y) {
        return 0.0;
    }
    return textureLoad(srcTex, p, 0).a;
}

// pass 1: 16-bit horizontal blur packed by pass 0
fn packedAt(p: vec2<i32>, dim: vec2<i32>) -> f32 {
    if (p.x < 0 || p.y < 0 || p.x >= dim.x || p.y >= dim.y) {
        return 0.0;
    }
    let t = textureLoad(srcTex, p, 0);
    return (round(t.r * 255.0) + t.g) / 255.0;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dim = vec2<i32>(textureDimensions(srcTex));
    let p = vec2<i32>(floor(in.pos.xy));
    let sig = sigmaOf();
    let r = radiusOf(sig);
    let inv2s2 = 1.0 / max(2.0 * sig * sig, 1e-6);

    if (P.passIndex < 0.5) {
        var acc = 0.0;
        var wsum = 0.0;
        for (var k = -r; k <= r; k = k + 1) {
            let w = exp(-f32(k * k) * inv2s2);
            acc = acc + w * alphaAt(p + vec2<i32>(k, 0), dim);
            wsum = wsum + w;
        }
        let v = clamp(acc / wsum, 0.0, 1.0) * 255.0;
        let hi = floor(v);
        return vec4<f32>(hi / 255.0, v - hi, 0.0, 1.0);
    }

    var acc = 0.0;
    var wsum = 0.0;
    for (var k = -r; k <= r; k = k + 1) {
        let w = exp(-f32(k * k) * inv2s2);
        acc = acc + w * packedAt(p + vec2<i32>(0, k), dim);
        wsum = wsum + w;
    }
    let b = clamp(acc / wsum, 0.0, 1.0);

    let c = abs(P.choke);
    var a = b;
    if (c > 1e-4) {
        var t = 0.125;
        if (P.choke > 0.0) {
            t = 0.875;
        }
        let halfW = 0.375 / c;
        let lo = max(0.0, t - halfW);
        let hi = min(1.0, t + halfW);
        a = clamp((b - lo) / max(hi - lo, 1e-6), 0.0, 1.0);
    }

    if (P.view > 1.5) {
        return vec4<f32>(a, a, a, 1.0);
    }
    let o = textureLoad(origTex, p, 0);
    var straight = vec3<f32>(0.0);
    if (o.a > 0.0) {
        straight = clamp(o.rgb / o.a, vec3<f32>(0.0), vec3<f32>(1.0));
    }
    return vec4<f32>(straight * a, a);
}
