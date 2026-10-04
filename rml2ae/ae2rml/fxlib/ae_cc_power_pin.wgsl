// After Effects "CC Power Pin" (CC Power Pin) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Reuses the MEASURED Corner Pin law unchanged (projective map of the layer onto the four corners, area-sampled):
// CC Power Pin at its default Perspective 100 % and Expansion 0 is assumed to be the same map. Perspective < 100 %
// (towards a bilinear map) and Expansion are not modelled. Corner fields keep Corner Pin's names.
struct Params {
    size: vec2<f32>,
    ul: vec2<f32>,           // AE 1 Upper Left (point, layer px)
    ur: vec2<f32>,           // AE 2 Upper Right (point, layer px)
    ll: vec2<f32>,           // AE 3 Lower Left (point, layer px)
    lr: vec2<f32>,           // AE 4 Lower Right (point, layer px)
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

const MAXPAIRS: i32 = 12;                                   // exact box up to 24 layer px per axis (144 taps max)

// Box [a, b] over the layer (texel k covers [k, k+1]) as weighted bilinear taps: two adjacent texels are merged into
// one tap (pos = k + 0.5 + w1 / (w0 + w1)), texels outside the layer weigh 0 (transparent). Wider boxes fall back to
// MAXPAIRS evenly spaced bilinear taps (approximate box).
struct Taps {
    pos: array<f32, 12>,
    wt: array<f32, 12>,
    n: i32,
};

fn taps1d(a: f32, b: f32, n: f32) -> Taps {
    var t: Taps;
    t.n = 0;
    let k0 = floor(a);
    let k1 = ceil(b) - 1.0;
    if (k1 - k0 + 1.0 <= f32(2 * MAXPAIRS)) {
        var k = k0;
        for (var i = 0; i < MAXPAIRS; i = i + 1) {
            if (k > k1) { break; }
            var w0 = min(b, k + 1.0) - max(a, k);
            var w1 = select(0.0, min(b, k + 2.0) - max(a, k + 1.0), k + 1.0 <= k1);
            if (k < 0.0 || k >= n) { w0 = 0.0; }
            if (k + 1.0 < 0.0 || k + 1.0 >= n) { w1 = 0.0; }
            let w = w0 + w1;
            if (w > 0.0) {
                t.pos[t.n] = k + 0.5 + w1 / w;
                t.wt[t.n] = w;
                t.n = t.n + 1;
            }
            k = k + 2.0;
        }
    } else {
        let L = (b - a) / f32(MAXPAIRS);
        for (var i = 0; i < MAXPAIRS; i = i + 1) {
            let c = a + (f32(i) + 0.5) * L;
            if (c >= 0.0 && c <= n) {
                t.pos[t.n] = clamp(c, 0.5, n - 0.5);
                t.wt[t.n] = L;
                t.n = t.n + 1;
            }
        }
    }
    return t;
}

fn texel(x: f32, y: f32) -> vec4<f32> {
    let n = vec2<i32>(P.size) - vec2<i32>(1);
    return textureLoad(srcTex, clamp(vec2<i32>(i32(x), i32(y)), vec2<i32>(0), n), 0);
}

// a merged tap read with exact float weights (4 texel loads) instead of the GPU's fixed-point bilinear filter
fn exactTap(pos: vec2<f32>) -> vec4<f32> {
    let k = floor(pos - 0.5);
    let fr = pos - 0.5 - k;
    var top = texel(k.x, k.y) * (1.0 - fr.x);
    var bot = texel(k.x, k.y + 1.0) * (1.0 - fr.x);
    if (fr.x > 0.0) {
        top = top + texel(k.x + 1.0, k.y) * fr.x;
        bot = bot + texel(k.x + 1.0, k.y + 1.0) * fr.x;
    }
    if (fr.y > 0.0) {
        return top * (1.0 - fr.y) + bot * fr.y;
    }
    return top;
}

// mean of the layer over the box centred at X (texel k covers [k, k+1]) of size f
fn boxAvg(X: vec2<f32>, f: vec2<f32>) -> vec4<f32> {
    let r = 0.5 * f;
    let tx = taps1d(X.x - r.x, X.x + r.x, P.size.x);
    let ty = taps1d(X.y - r.y, X.y + r.y, P.size.y);
    let exact = tx.n * ty.n <= 16;                           // small boxes (<= 8x8 texels): exact loads
    var acc = vec4<f32>(0.0);
    for (var j = 0; j < MAXPAIRS; j = j + 1) {
        if (j >= ty.n) { break; }
        for (var i = 0; i < MAXPAIRS; i = i + 1) {
            if (i >= tx.n) { break; }
            let q = vec2<f32>(tx.pos[i], ty.pos[j]);
            var s: vec4<f32>;
            if (exact) {
                s = exactTap(q);
            } else {
                s = textureSampleLevel(srcTex, srcSamp, q / P.size, 0.0);
            }
            acc = acc + tx.wt[i] * ty.wt[j] * s;
        }
    }
    return acc / (f.x * f.y);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let p = in.uv * P.size - 0.5;                            // AE coords: pixel centres at integers
    // unit square -> quad (Heckbert): (u,v) = (0,0) UL, (1,0) UR, (1,1) LR, (0,1) LL
    let p0 = P.ul;
    let p1 = P.ur;
    let p2 = P.lr;
    let p3 = P.ll;
    let s = p0 - p1 + p2 - p3;
    var a = p1.x - p0.x;
    var b = p3.x - p0.x;
    var d = p1.y - p0.y;
    var e = p3.y - p0.y;
    var g = 0.0;
    var h = 0.0;
    if (abs(s.x) > 1e-6 || abs(s.y) > 1e-6) {
        let d1 = p1 - p2;
        let d2 = p3 - p2;
        let den = d1.x * d2.y - d2.x * d1.y;
        if (abs(den) < 1e-12) {
            return vec4<f32>(0.0);
        }
        g = (s.x * d2.y - d2.x * s.y) / den;
        h = (d1.x * s.y - s.x * d1.y) / den;
        a = p1.x - p0.x + g * p1.x;
        b = p3.x - p0.x + h * p3.x;
        d = p1.y - p0.y + g * p1.y;
        e = p3.y - p0.y + h * p3.y;
    }
    let c = p0.x;
    let f = p0.y;
    // inverse (adjugate) of [[a b c][d e f][g h 1]]
    let A = e - f * h;
    let B = c * h - b;
    let C = b * f - c * e;
    let D = f * g - d;
    let E = a - c * g;
    let F = c * d - a * f;
    let G = d * h - e * g;
    let H = b * g - a * h;
    let I = a * e - b * d;
    let w = G * p.x + H * p.y + I;
    if (abs(w) < 1e-12) {
        return vec4<f32>(0.0);
    }
    let u = (A * p.x + B * p.y + C) / w;
    let v = (D * p.x + E * p.y + F) / w;
    // the point is in front of the projection when the forward denominator g u + h v + 1 is positive
    if (g * u + h * v + 1.0 <= 0.0) {
        return vec4<f32>(0.0);
    }
    // Jacobian of the inverse map (layer px per output px)
    let dudx = (A - u * G) / w;
    let dudy = (B - u * H) / w;
    let dvdx = (D - v * G) / w;
    let dvdy = (E - v * H) / w;
    let fp = vec2<f32>((abs(dudx) + abs(dudy)) * P.size.x, (abs(dvdx) + abs(dvdy)) * P.size.y);
    let X = vec2<f32>(u, v) * P.size + 0.5;                  // texel k covers [k, k+1]
    let lo = X - 0.5 * max(fp, vec2<f32>(1.0));
    let hi = X + 0.5 * max(fp, vec2<f32>(1.0));
    if (hi.x <= 0.0 || hi.y <= 0.0 || lo.x >= P.size.x || lo.y >= P.size.y) {
        return vec4<f32>(0.0);
    }
    // plain GPU unorm rounding matches AE here (a +0.02/255 round-half-up bias measured worse: 0.002 vs 0.0005 mean)
    return boxAvg(X, max(fp, vec2<f32>(1.0)));
}
