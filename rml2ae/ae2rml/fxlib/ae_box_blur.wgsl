// After Effects "Fast Box Blur" (ADBE Box Blur2) — `Iterations` successive box blurs of radius `Blur Radius`,
// separable (horizontal then vertical), on the PREMULTIPLIED layer. Measured against AE 2026 8 bpc:
//   * one iteration = a box of width 2r+1 centred on the pixel (r = 8 -> ramps of 255/17 = 15 per pixel);
//   * n iterations = the box convolved n times, accumulated in float (no 8-bit rounding between iterations: the
//     float model fits AE to 0.005 % mean / max 1 level for n = 1 and 0.018 % / max 2 for n = 3);
//   * Repeat Edge Pixels OFF: pixels outside the layer are transparent; ON: the edge pixels are repeated (clamp);
//   * Blur Dimensions 1 = horizontal and vertical, 2 = horizontal only, 3 = vertical only.
// A fractional radius r = N + f is taken as the box -N..N weight 1 plus the taps ±(N+1) weight f (the model measured
// on AE's Drop Shadow softness, which uses the same box blur); not measured on this effect (refs use integer radii).
// 2 passes: pass 0 = horizontal, pass 1 = vertical (the intermediate is RGBA8 in Rive -> one 8-bit rounding).
// The n-fold kernel is evaluated per tap: closed form for n <= 3 (exact), trinomial sum for r < 1 (exact), else an
// order-2 Edgeworth expansion of the n-fold box (<= 0.45/255 off the exact kernel for n >= 4, r >= 1).
// Only the taps inside the layer are read, in bilinear pairs sampled at their weighted centre (exact); the result is
// normalised by the kernel's analytic total, and with Repeat Edge Pixels the weight beyond each edge goes to the edge
// pixel. Exact up to 2*MAX_TAPS = 600 texels of support inside the layer; wider (iterations x radius > 300 px on a
// layer wider/taller than 600 px) the taps are grouped by g texels read at the group centre (approximation).
struct Params {
    size: vec2<f32>,
    radius: f32,          // AE 1 Blur Radius px 0..30000 (default 0)
    iterations: f32,      // AE 2 Iterations 1..50 (default 3)
    dimensions: f32,      // AE 3 Blur Dimensions popup 1 = Horizontal and Vertical, 2 = Horizontal, 3 = Vertical (default 1)
    repeatEdge: f32,      // AE 4 Repeat Edge Pixels checkbox 0/1 (default 1)
    passIndex: f32,
    pad1: f32,
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

const MAX_TAPS: i32 = 300;          // bilinear taps per pass (each covers 2 texels)

// ------------------------------------------------------------------ kernel of n boxes of radius N + f
struct Kernel {
    n: i32,         // iterations
    N: f32,         // integer part of the radius
    f: f32,         // fractional part
    mode: i32,      // 1..3 closed form, 4 trinomial (N = 0), 5 Edgeworth
    sigma: f32,     // Edgeworth: std dev of the n-fold box
    c4: f32,        // K4 / (24 s^4)
    c6: f32,        // K6 / (720 s^6)
    c8: f32,        // K4^2 / (1152 s^8)
};

fn c2(x: f32) -> f32 {                       // binomial(x, 2), 0 below 2
    return select(0.0, x * (x - 1.0) * 0.5, x >= 2.0);
}

fn ind(b: bool) -> f32 {
    return select(0.0, 1.0, b);
}

fn k1(j: f32, N: f32, f: f32) -> f32 {
    let aj = abs(j);
    return ind(aj <= N) + f * ind(aj == N + 1.0);
}

fn k2(j: f32, N: f32, f: f32) -> f32 {
    let m = 2.0 * N + 1.0;
    let q = N + 1.0;
    let aj = abs(j);
    var v = max(0.0, m - aj);
    v += 2.0 * f * (ind(abs(j - q) <= N) + ind(abs(j + q) <= N));
    v += f * f * (ind(aj == 2.0 * q) + 2.0 * ind(aj == 0.0));
    return v;
}

// three convolved boxes (same closed form as ae_drop_shadow.wgsl)
fn k3(j: f32, N: f32, f: f32) -> f32 {
    let m = 2.0 * N + 1.0;
    let q = N + 1.0;
    let aj = abs(j);
    let t = j + 3.0 * N + 2.0;
    var v = c2(t) - 3.0 * c2(t - m) + 3.0 * c2(t - 2.0 * m) - c2(t - 3.0 * m);
    v += 3.0 * f * (max(0.0, m - abs(j + q)) + max(0.0, m - abs(j - q)));
    let u0 = ind(aj <= N);
    let um = ind(abs(j + 2.0 * q) <= N);
    let up = ind(abs(j - 2.0 * q) <= N);
    v += 3.0 * f * f * (um + 2.0 * u0 + up);
    let e3 = ind(aj == 3.0 * q) + 3.0 * ind(aj == q);
    v += f * f * f * e3;
    return v;
}

// n-fold [f, 1, f] (radius < 1): sum over c of n! / ((a+c)! c! (n-a-2c)!) f^(a+2c), a = |j|
fn ktri(j: f32, n: i32, f: f32) -> f32 {
    let a = i32(abs(j));
    if (a > n) {
        return 0.0;
    }
    var t = 1.0;                              // C(n, a) f^a
    for (var i = 0; i < a; i++) {
        t *= f * f32(n - i) / f32(i + 1);
    }
    var s = t;
    var c = 0;
    loop {
        let rem = n - a - 2 * c;
        if (rem < 2) { break; }
        t *= f * f * f32(rem) * f32(rem - 1) / (f32(c + a + 1) * f32(c + 1));
        s += t;
        c++;
    }
    return s;
}

fn kedge(j: f32, K: Kernel) -> f32 {
    let z = j / K.sigma;
    let z2 = z * z;
    let he4 = (z2 - 6.0) * z2 + 3.0;
    let he6 = ((z2 - 15.0) * z2 + 45.0) * z2 - 15.0;
    let he8 = (((z2 - 28.0) * z2 + 210.0) * z2 - 420.0) * z2 + 105.0;
    return exp(-0.5 * z2) * (1.0 + K.c4 * he4 + K.c6 * he6 + K.c8 * he8);
}

fn kw(j: f32, K: Kernel) -> f32 {
    if (K.mode == 1) { return k1(j, K.N, K.f); }
    if (K.mode == 2) { return k2(j, K.N, K.f); }
    if (K.mode == 3) { return k3(j, K.N, K.f); }
    if (K.mode == 4) { return ktri(j, K.n, K.f); }
    return kedge(j, K);
}

fn makeKernel(r: f32, n: i32) -> Kernel {
    var K: Kernel;
    K.n = n;
    K.N = floor(r);
    K.f = r - K.N;
    K.sigma = 1.0;
    K.c4 = 0.0;
    K.c6 = 0.0;
    K.c8 = 0.0;
    if (n <= 3) {
        K.mode = n;
    } else if (K.N < 0.5) {
        K.mode = 4;
    } else {
        K.mode = 5;
        // cumulants of one box (weights 1 on -N..N, f on ±(N+1)), then of the n-fold sum
        let N = K.N;
        let f = K.f;
        let q = N + 1.0;
        let mm = 2.0 * N + 1.0 + 2.0 * f;
        let s2 = N * q * (2.0 * N + 1.0) / 6.0;
        let s4 = N * q * (2.0 * N + 1.0) * (3.0 * N * N + 3.0 * N - 1.0) / 30.0;
        let s6 = N * q * (2.0 * N + 1.0) * (3.0 * N * N * N * N + 6.0 * N * N * N - 3.0 * N + 1.0) / 42.0;
        let q2 = q * q;
        let m2 = (2.0 * s2 + 2.0 * f * q2) / mm;
        let m4 = (2.0 * s4 + 2.0 * f * q2 * q2) / mm;
        let m6 = (2.0 * s6 + 2.0 * f * q2 * q2 * q2) / mm;
        let nn = f32(n);
        let v = nn * m2;
        let k4 = nn * (m4 - 3.0 * m2 * m2);
        let k6 = nn * (m6 - 15.0 * m4 * m2 + 30.0 * m2 * m2 * m2);
        K.sigma = sqrt(v);
        K.c4 = k4 / (24.0 * v * v);
        K.c6 = k6 / (720.0 * v * v * v);
        K.c8 = k4 * k4 / (1152.0 * v * v * v * v);
    }
    return K;
}

fn erfApprox(x: f32) -> f32 {                 // Abramowitz & Stegun 7.1.26, |error| < 1.5e-7, x >= 0
    let t = 1.0 / (1.0 + 0.3275911 * x);
    let y = ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t;
    return 1.0 - y * exp(-x * x);
}

// sum of the kernel over its whole support -R..R (normalisation, and the weight beyond the canvas edges)
fn ktotal(K: Kernel, R: i32) -> f32 {
    let nn = f32(K.n);
    if (K.mode <= 3) {
        return pow(2.0 * K.N + 1.0 + 2.0 * K.f, nn);
    }
    if (K.mode == 4) {
        return pow(1.0 + 2.0 * K.f, nn);
    }
    if (R <= 64) {
        var s = kedge(0.0, K);
        for (var j = 1; j <= R; j++) {
            s += 2.0 * kedge(f32(j), K);
        }
        return s;
    }
    // integral of the Edgeworth density over -(R+0.5)..R+0.5 (∫ φ He_k = -φ He_(k-1))
    let Z = (f32(R) + 0.5) / K.sigma;
    let Z2 = Z * Z;
    let he3 = (Z2 - 3.0) * Z;
    let he5 = ((Z2 - 10.0) * Z2 + 15.0) * Z;
    let he7 = (((Z2 - 21.0) * Z2 + 105.0) * Z2 - 105.0) * Z;
    let g = 2.5066282746 * erfApprox(Z * 0.7071067812) - 2.0 * exp(-0.5 * Z2) * (K.c4 * he3 + K.c6 * he5 + K.c8 * he7);
    return K.sigma * g;
}

// ------------------------------------------------------------------ sampling along the blur axis
// texel x (float, texel-index space: texel i has its centre at i) on the axis, row/column `o` across it
fn uvAt(x: f32, o: f32, horizontal: bool, dimsF: vec2<f32>) -> vec2<f32> {
    if (horizontal) {
        return vec2<f32>((x + 0.5) / dimsF.x, (o + 0.5) / dimsF.y);
    }
    return vec2<f32>((o + 0.5) / dimsF.x, (x + 0.5) / dimsF.y);
}

fn loadAt(i: i32, o: i32, horizontal: bool) -> vec4<f32> {
    if (horizontal) {
        return textureLoad(srcTex, vec2<i32>(i, o), 0);
    }
    return textureLoad(srcTex, vec2<i32>(o, i), 0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let dimsF = vec2<f32>(dims);
    let px = vec2<i32>(floor(in.uv * dimsF));
    let horizontal = P.passIndex < 0.5;
    let d = i32(round(P.dimensions));
    let blurOn = select(d != 2, d != 3, horizontal);
    let r = clamp(P.radius, 0.0, 30000.0);
    let n = clamp(i32(round(P.iterations)), 1, 50);
    if (!blurOn || r <= 0.0) {
        return textureLoad(srcTex, px, 0);
    }
    let K = makeKernel(r, n);
    let R = n * i32(K.N + select(0.0, 1.0, K.f > 0.0));
    let wtot = ktotal(K, R);
    if (R <= 0 || !(wtot > 0.0)) {
        return textureLoad(srcTex, px, 0);
    }
    let p = select(px.y, px.x, horizontal);         // coordinate along the blur axis
    let o = select(px.x, px.y, horizontal);         // coordinate across it
    let len = select(dims.y, dims.x, horizontal);
    // offsets j whose texel p + j lies inside the layer; the others are transparent (or the edge pixel, repeated)
    let a = max(-R, -p);
    let b = min(R, len - 1 - p);
    let cnt = b - a + 1;
    var acc = vec4<f32>(0.0);
    var sNeg = 0.0;                                 // kernel weight read at j < 0 / j > 0
    var sPos = 0.0;
    if (cnt <= 2 * MAX_TAPS) {
        // exact: every texel weighted, read in bilinear pairs sampled at their weighted centre
        var j = a;
        loop {
            if (j > b) { break; }
            let w0 = kw(f32(j), K);
            let has1 = j + 1 <= b;
            let w1 = select(0.0, kw(f32(j + 1), K), has1);
            let ws = w0 + w1;
            if (ws > 0.0 && w0 >= 0.0 && w1 >= 0.0) {
                let x = f32(p + j) + w1 / ws;
                acc += ws * textureSampleLevel(srcTex, srcSamp, uvAt(x, f32(o), horizontal, dimsF), 0.0);
            } else {
                acc += w0 * loadAt(p + j, o, horizontal);
                if (has1) {
                    acc += w1 * loadAt(p + j + 1, o, horizontal);
                }
            }
            if (j < 0) { sNeg += w0; } else if (j > 0) { sPos += w0; }
            if (j + 1 < 0) { sNeg += w1; } else if (j + 1 > 0) { sPos += w1; }
            j += 2;
        }
    } else {
        // approximation (support wider than 2*MAX_TAPS texels inside the layer): groups of g texels, each read
        // at its centre with g times the centre weight
        let g = (cnt + MAX_TAPS - 1) / MAX_TAPS;
        var j = a;
        loop {
            if (j > b) { break; }
            let c = min(g, b - j + 1);
            let cc = f32(j) + 0.5 * f32(c - 1);
            let w = kw(round(cc), K) * f32(c);
            acc += w * textureSampleLevel(srcTex, srcSamp, uvAt(f32(p) + cc, f32(o), horizontal, dimsF), 0.0);
            let nNeg = clamp(-j, 0, c);
            let nPos = clamp(j + c - 1, 0, c);
            sNeg += w * f32(nNeg) / f32(c);
            sPos += w * f32(nPos) / f32(c);
            j += g;
        }
    }
    if (P.repeatEdge > 0.5) {
        // Repeat Edge Pixels: the weight falling beyond each edge goes to the edge pixel (kernel is symmetric)
        let half = 0.5 * (wtot - kw(0.0, K));
        if (a > -R) {
            acc += max(0.0, half - sNeg) * loadAt(0, o, horizontal);
        }
        if (b < R) {
            acc += max(0.0, half - sPos) * loadAt(len - 1, o, horizontal);
        }
    }
    return clamp(acc / wtot, vec4<f32>(0.0), vec4<f32>(1.0));
}
