// After Effects "Luma Key" (ADBE Luma Key, FR "Incrustation Luminance") — measured on AE 2026 8 bpc.
//   * luminance of the STRAIGHT 8-bit colour: L = 0.30 R + 0.59 G + 0.11 B (0..255, NOT rounded; Rec.601 0.299/0.587
//     and Rec.709 leave hundreds of pixels on the wrong side of the threshold); transparent pixels count as black.
//   * Key Type 1 Key Out Brighter: keyed when L > Threshold; 2 Key Out Darker: keyed when L < Threshold (L ==
//     Threshold is kept, both measured bit-exact); Tolerance plays no role for these two (measured).
//     3 Key Out Similar: keyed when |L - Threshold| <= Tolerance; 4 Key Out Dissimilar: the complement (unmeasured).
//   * the binary matte (1 = kept) is thinned by Edge Thin px (> 0 erodes, < 0 dilates, square window; unmeasured),
//     then feathered by a separable blur: three iterated boxes of fractional radii (0.494, 0.349, 0.194) x Edge
//     Feather px (fitted on the Feather 10 ref; the linear scaling with Feather is a model); the matte outside the
//     layer is the matte of transparent black (e.g. kept by Key Out Brighter: the feather then bleeds inwards at
//     the layer edges, as in AE).
//   * output alpha = floor(alpha8 * matte) (AE truncates: complementary steps sum to 254), straight colour kept.
// Passes: 0 = matte + horizontal thin, 1 = vertical thin, 2 = horizontal blur (16-bit fixed point packed in R/G),
// 3 = vertical blur + composite with origTex.
struct Params {
    size: vec2<f32>,
    keyType: f32,         // AE 1 Key Type 1..4 (1 Key Out Brighter, 2 Key Out Darker, 3 Key Out Similar, 4 Key Out Dissimilar)
    threshold: f32,       // AE 2 Threshold 0..255
    tolerance: f32,       // AE 3 Tolerance 0..255
    edgeThin: f32,        // AE 4 Edge Thin -5..5 px
    edgeFeather: f32,     // AE 5 Edge Feather 0..100 px
    passIndex: f32,
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

const MAXT: i32 = 255;    // taps per pass; wider kernels are sampled every `stride` px

// straight 8-bit colour of a premultiplied texel
fn straight8(s: vec4<f32>) -> vec3<f32> {
    if (s.a <= 0.0) {
        return vec3<f32>(0.0);
    }
    return clamp(round(s.rgb / s.a * 255.0), vec3<f32>(0.0), vec3<f32>(255.0));
}

// 1 = kept, 0 = keyed out, from the straight 8-bit colour
fn matteOf(c8: vec3<f32>) -> f32 {
    let l100 = 30.0 * c8.r + 59.0 * c8.g + 11.0 * c8.b;     // 100 x luminance, exact integer
    let t100 = P.threshold * 100.0;
    let tol100 = max(P.tolerance, 0.0) * 100.0;
    let kt = i32(round(P.keyType));
    var keyed = false;
    if (kt == 1) {
        keyed = l100 > t100 + 0.5;
    } else if (kt == 2) {
        keyed = l100 < t100 - 0.5;
    } else if (kt == 3) {
        keyed = abs(l100 - t100) <= tol100 + 0.5;
    } else {
        keyed = abs(l100 - t100) > tol100 + 0.5;
    }
    return select(1.0, 0.0, keyed);
}

fn inside(p: vec2<i32>, dim: vec2<i32>) -> bool {
    return p.x >= 0 && p.y >= 0 && p.x < dim.x && p.y < dim.y;
}

fn matteAt(p: vec2<i32>, dim: vec2<i32>) -> f32 {
    if (!inside(p, dim)) {
        return matteOf(vec3<f32>(0.0));
    }
    return matteOf(straight8(textureLoad(srcTex, p, 0)));
}

fn plainAt(p: vec2<i32>, dim: vec2<i32>) -> f32 {
    if (!inside(p, dim)) {
        return matteOf(vec3<f32>(0.0));
    }
    return textureLoad(srcTex, p, 0).r;
}

fn packedAt(p: vec2<i32>, dim: vec2<i32>) -> f32 {
    if (!inside(p, dim)) {
        return matteOf(vec3<f32>(0.0));
    }
    let t = textureLoad(srcTex, p, 0);
    return (round(t.r * 255.0) + t.g) / 255.0;
}

fn pack16(v: f32) -> vec4<f32> {
    let x = clamp(v, 0.0, 1.0) * 255.0;
    let hi = floor(x);
    return vec4<f32>(hi / 255.0, x - hi, 0.0, 1.0);
}

// three fractional boxes (weight 1 on |x| <= floor(rho), frac(rho) on floor(rho) + 1): their convolution K(u) is the
// triple running sum of the 64 impulses of (f + g z - g z^N - f z^(N+1)) per box, N = 2 floor(rho) + 2, g = 1 - f
fn radii() -> vec3<f32> {
    return vec3<f32>(0.494, 0.349, 0.194) * clamp(P.edgeFeather, 0.0, 1000.0);
}

fn impPos(rho: f32, k: i32) -> f32 {
    let n = floor(rho);
    switch k {
        case 0: { return 0.0; }
        case 1: { return 1.0; }
        case 2: { return 2.0 * n + 2.0; }
        default: { return 2.0 * n + 3.0; }
    }
}

fn impW(rho: f32, k: i32) -> f32 {
    let f = rho - floor(rho);
    switch k {
        case 0: { return f; }
        case 1: { return 1.0 - f; }
        case 2: { return f - 1.0; }
        default: { return -f; }
    }
}

// K(u) unnormalised (sum = prod(2 rho + 1)); u is the offset from the kernel centre, s0 = its first tap (<= 0)
fn kernelAt(u: f32, r: vec3<f32>, s0: f32) -> f32 {
    var acc = 0.0;
    for (var a = 0; a < 4; a = a + 1) {
        for (var b = 0; b < 4; b = b + 1) {
            let pab = impPos(r.x, a) + impPos(r.y, b);
            let wab = impW(r.x, a) * impW(r.y, b);
            for (var c = 0; c < 4; c = c + 1) {
                let m = u - s0 - pab - impPos(r.z, c);
                if (m >= 0.0) {
                    acc = acc + wab * impW(r.z, c) * (m + 1.0) * (m + 2.0) * 0.5;
                }
            }
        }
    }
    return acc;
}

fn blurAxis(p: vec2<i32>, dim: vec2<i32>, axis: vec2<i32>, fromPacked: bool) -> f32 {
    let r = radii();
    let s0 = -(floor(r.x) + floor(r.y) + floor(r.z) + 3.0);
    let taps = i32(-2.0 * s0 + 1.0);
    let stride = max(1, (taps + MAXT - 1) / MAXT);
    var acc = 0.0;
    var wsum = 0.0;
    for (var j = 0; j < taps; j = j + stride) {
        let u = s0 + f32(j + stride / 2);
        let w = kernelAt(u, r, s0);
        if (w != 0.0) {
            let q = p + axis * i32(u);
            var v: f32;
            if (fromPacked) {
                v = packedAt(q, dim);
            } else {
                v = plainAt(q, dim);
            }
            acc = acc + w * v;
            wsum = wsum + w;
        }
    }
    return acc / max(wsum, 1e-20);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dim = vec2<i32>(textureDimensions(srcTex, 0));
    let p = vec2<i32>(floor(in.pos.xy));
    let keep = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0).a * 0.0;   // keeps binding 1 in the auto layout
    let pi = i32(round(P.passIndex));
    let thin = i32(round(clamp(P.edgeThin, -5.0, 5.0)));
    let n = abs(thin);
    if (pi == 0 || pi == 1) {
        var m = matteAt(p, dim);
        if (pi == 1) {
            m = plainAt(p, dim);
        }
        for (var k = -n; k <= n; k = k + 1) {
            var v: f32;
            if (pi == 0) {
                v = matteAt(p + vec2<i32>(k, 0), dim);
            } else {
                v = plainAt(p + vec2<i32>(0, k), dim);
            }
            if (thin > 0) {
                m = min(m, v);
            } else {
                m = max(m, v);
            }
        }
        return vec4<f32>(m + keep, 0.0, 0.0, 1.0);
    }
    if (pi == 2) {
        return pack16(blurAxis(p, dim, vec2<i32>(1, 0), false) + keep);
    }
    let g = clamp(blurAxis(p, dim, vec2<i32>(0, 1), true), 0.0, 1.0);
    let o = textureLoad(origTex, clamp(p, vec2<i32>(0), dim - vec2<i32>(1)), 0);
    let a8 = round(o.a * 255.0);
    let na = floor(a8 * g + 1e-3);
    let c8 = straight8(o);
    let pm = floor((c8 * na + 127.0) / 255.0);                           // AE 8-bit premultiply
    return vec4<f32>(pm / 255.0, na / 255.0);
}
