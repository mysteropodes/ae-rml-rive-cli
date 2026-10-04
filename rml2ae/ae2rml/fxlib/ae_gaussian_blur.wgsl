// After Effects "Gaussian Blur" (ADBE Gaussian Blur 2, AE 2026) as a 2-pass separable blur.
// Measured against AE (8 bpc, 640x360, Blurriness 10/20/30, both Repeat Edge modes, H-only) — mean error < 0.06 %:
//  * AE blurs in a LINEARISED space: straight colour -> pow(c, 2.4) -> premultiply -> blur -> unpremultiply ->
//    pow(c, 1/2.4) -> premultiply (the legacy "ADBE Gaussian Blur" does not linearise; this one does).
//  * The kernel is NOT a sampled Gaussian: it is the impulse response of a recursive (IIR) filter,
//    K(x) = (a cos(w u) + b sin(w u)) exp(-c u), u = |x| / Blurriness, normalised to sum 1 — Gaussian-like core
//    (sigma ~ 0.2825 x Blurriness), negative lobe between 0.9 and 1.8 x Blurriness (dark halos next to bright edges),
//    support ~1.9 x Blurriness. a, b, c, w fitted by least squares on the AE renders.
//  * Repeat Edge Pixels OFF: outside the layer is transparent; ON: edge pixels repeated (coordinates clamped).
// Pass 0 (Horizontal and Vertical only): horizontal blur -> intermediate RGBA8 encoding (premultiplied linear values
// with a small negative/over-range headroom, cube-root coded for dark precision). Pass 1: vertical blur of the
// intermediate, or the single-direction blur straight from origTex for Horizontal / Vertical.
// Exact per-pixel taps up to 150 per side (Blurriness <= 78); above, 301 bilinear taps at a fractional stride
// (bilinear mixes neighbouring texels in the encoded domain: approximation for very large blurs).
struct Params {
    size: vec2<f32>,
    blurriness: f32,      // AE 1 Blurriness 0..30000 (kernel scale in layer px; support ~1.9 x Blurriness)
    dimensions: f32,      // AE 2 Blur Dimensions popup 1 = Horizontal and Vertical, 2 = Horizontal, 3 = Vertical
    repeatEdge: f32,      // AE 3 Repeat Edge Pixels checkbox 0/1
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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

// kernel K(u) = (KA cos(KW u) + KB sin(KW u)) exp(-KC u), u = |x| / Blurriness
const KA: f32 = 1.35693;
const KB: f32 = 2.73784;
const KC: f32 = 4.40237;
const KW: f32 = 2.95984;
const SPAN: f32 = 1.9;            // support in Blurriness units (|K| < 1e-4 beyond)
const MAX_TAPS: i32 = 150;        // per side
const GAMMA: f32 = 2.4;           // AE's linearisation
// intermediate (pass 0 -> pass 1) coding: rgb = cbrt((v + MID_C) / MID_S) (0 -> code 51, 1 -> code 254 exactly),
// alpha = (a + MID_CA) / MID_SA (0 -> code 4, 1 -> code 251 exactly); keeps the kernel's negative undershoot.
const MID_C: f32 = 0.0081609;
const MID_S: f32 = 1.0201153;
const MID_CA: f32 = 0.0161943;
const MID_SA: f32 = 1.0323887;

fn decodeSrc(t: vec4<f32>) -> vec4<f32> {          // premultiplied 8-bit -> premultiplied linear
    if (t.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    let s = clamp(t.rgb / t.a, vec3<f32>(0.0), vec3<f32>(1.0));
    return vec4<f32>(pow(s, vec3<f32>(GAMMA)) * t.a, t.a);
}

fn decodeMid(t: vec4<f32>) -> vec4<f32> {
    return vec4<f32>(t.rgb * t.rgb * t.rgb * MID_S - MID_C, t.a * MID_SA - MID_CA);
}

fn encodeMid(v: vec4<f32>) -> vec4<f32> {
    let q = clamp((v.rgb + MID_C) / MID_S, vec3<f32>(0.0), vec3<f32>(1.0));
    return vec4<f32>(pow(q, vec3<f32>(1.0 / 3.0)), clamp((v.a + MID_CA) / MID_SA, 0.0, 1.0));
}

fn encodeOut(v: vec4<f32>) -> vec4<f32> {           // premultiplied linear -> premultiplied 8-bit space
    if (v.a <= 1e-6) {
        return vec4<f32>(0.0);
    }
    let a = min(v.a, 1.0);
    let s = clamp(max(v.rgb, vec3<f32>(0.0)) / v.a, vec3<f32>(0.0), vec3<f32>(1.0));
    return vec4<f32>(pow(s, vec3<f32>(1.0 / GAMMA)) * a, a);
}

// texel p of the chosen source (0 = srcTex as content, 1 = srcTex as intermediate, 2 = origTex), premultiplied linear
fn texel(which: i32, p: vec2<i32>, dims: vec2<i32>, clampEdge: bool) -> vec4<f32> {
    var q = p;
    if (clampEdge) {
        q = clamp(p, vec2<i32>(0), dims - vec2<i32>(1));
    } else if (p.x < 0 || p.y < 0 || p.x >= dims.x || p.y >= dims.y) {
        return vec4<f32>(0.0);
    }
    if (which == 2) {
        return decodeSrc(textureLoad(origTex, q, 0));
    }
    let t = textureLoad(srcTex, q, 0);
    if (which == 1) {
        return decodeMid(t);
    }
    return decodeSrc(t);
}

// bilinear sample at texel-space position x (centre of texel i = i + 0.5), for the strided path
fn sampleAt(which: i32, x: vec2<f32>, sz: vec2<f32>, clampEdge: bool) -> vec4<f32> {
    if (!clampEdge && (x.x < 0.0 || x.y < 0.0 || x.x > sz.x || x.y > sz.y)) {
        return vec4<f32>(0.0);
    }
    let uv = x / sz;
    if (which == 2) {
        return decodeSrc(textureSampleLevel(origTex, srcSamp, uv, 0.0));
    }
    let t = textureSampleLevel(srcTex, srcSamp, uv, 0.0);
    if (which == 1) {
        return decodeMid(t);
    }
    return decodeSrc(t);
}

fn cmul(a: vec2<f32>, b: vec2<f32>) -> vec2<f32> {
    return vec2<f32>(a.x * b.x - a.y * b.y, a.x * b.y + a.y * b.x);
}

// 1-D blur along dir through pixel p, result premultiplied linear
fn blur1(which: i32, p: vec2<i32>, dir: vec2<i32>, dims: vec2<i32>, clampEdge: bool) -> vec4<f32> {
    let B = P.blurriness;
    let R = i32(ceil(SPAN * B));
    var acc = KA * texel(which, p, dims, clampEdge);
    var wsum = KA;
    if (R <= MAX_TAPS) {
        let th = KW / B;
        let step = exp(-KC / B) * vec2<f32>(cos(th), sin(th));
        var z = vec2<f32>(1.0, 0.0);
        for (var i = 1; i <= R; i = i + 1) {
            z = cmul(z, step);
            let w = KA * z.x + KB * z.y;
            acc = acc + w * (texel(which, p + dir * i, dims, clampEdge) + texel(which, p - dir * i, dims, clampEdge));
            wsum = wsum + 2.0 * w;
        }
    } else {
        let sz = vec2<f32>(dims);
        let s = SPAN * B / f32(MAX_TAPS);           // fractional stride (> 1 px)
        let th = KW * s / B;
        let step = exp(-KC * s / B) * vec2<f32>(cos(th), sin(th));
        let c = vec2<f32>(p) + vec2<f32>(0.5);
        let d = vec2<f32>(dir) * s;
        var z = vec2<f32>(1.0, 0.0);
        for (var i = 1; i <= MAX_TAPS; i = i + 1) {
            z = cmul(z, step);
            let w = KA * z.x + KB * z.y;
            let o = d * f32(i);
            acc = acc + w * (sampleAt(which, c + o, sz, clampEdge) + sampleAt(which, c - o, sz, clampEdge));
            wsum = wsum + 2.0 * w;
        }
    }
    return acc / wsum;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let p = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0), dims - vec2<i32>(1));
    let mode = i32(round(P.dimensions));             // 1 = H and V, 2 = H, 3 = V
    let clampEdge = P.repeatEdge > 0.5;
    let blurOn = P.blurriness > 0.0;
    if (P.passIndex < 0.5) {
        if (blurOn && mode != 2 && mode != 3) {
            return encodeMid(blur1(0, p, vec2<i32>(1, 0), dims, clampEdge));
        }
        return textureLoad(srcTex, p, 0);                // unused by pass 1
    }
    if (!blurOn) {
        return textureLoad(origTex, p, 0);
    }
    if (mode == 2) {
        return encodeOut(blur1(2, p, vec2<i32>(1, 0), dims, clampEdge));
    }
    if (mode == 3) {
        return encodeOut(blur1(2, p, vec2<i32>(0, 1), dims, clampEdge));
    }
    return encodeOut(blur1(1, p, vec2<i32>(0, 1), dims, clampEdge));
}
