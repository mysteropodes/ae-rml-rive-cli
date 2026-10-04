// After Effects "CC Radial Fast Blur" (CC Radial Fast Blur) — a one-sided zoom blur streaking the layer away from
// Center. Measured against AE 2026 8 bpc (fxref cc_radial_fast_blur_0/1/2: Standard, Brightest, Darkest):
//   * AE runs a recursive (IIR) filter outward from Center over square rings: a pixel at Chebyshev distance
//     m = max(|dx|, |dy|) (pixel centres, layer px) reads its predecessor q = p - d / m (one pixel towards Center along
//     the major axis, the minor coordinate interpolated linearly between the two pixels of that column/row of the
//     already filtered OUTPUT — this repeated interpolation is what makes the streaks soften sideways);
//     the ring m < 1 around Center is the layer itself;
//   * Standard: out = (1 - w) src + w out(q); Brightest: the same mix, kept only where its R+G+B is larger than the
//     source pixel's (otherwise the source pixel); Darkest: kept only where smaller (decision per pixel, not per
//     channel; premultiplied RGBA); transparent outside the layer;
//   * w(m) = W - 1.43 / m^1.19 (clamped >= 0: no blur right at Center), W = exp(-0.1003 * (-ln(Amount/100))^0.976)
//     (fits Amount 50 -> 0.9323, 60 -> 0.9493, 80 -> 0.9771);
//   * the result is truncated (floor) to 8 bits, not rounded.
// GPU form: NPASS passes; pass i finalises the rings [i K, (i + 1) K) (K = ceil((max ring + 1) / NPASS)); a pixel
// redoes the recursion over the cone of pixels it depends on, from the previous pass's (final) rings, so the sideways
// interpolation is reproduced exactly except within a few px of the diagonals (where AE switches the stepping axis).
// Intermediate passes store floor(value); reads add half a level back. Layers whose rings exceed NPASS * KMAX (e.g.
// wider than ~2000 px with a centred Center) fall back to a single-pass ray march without the sideways softening.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 1 Center (point, layer px)
    amount: f32,          // AE 2 Amount 0..100 (default 50)
    zoom: f32,            // AE 3 Zoom popup 1 Standard, 2 Brightest, 3 Darkest
    passIndex: f32,
    pad0: f32,
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

const NPASS: i32 = 64;          // must match "passes" in cc_radial_fast_blur.json
const KMAX: i32 = 16;           // rings per pass handled by the cone recursion
const LMAX: i32 = 28;           // cone depth / width bound (KMAX + 1 + KMAX / 2 + 3)
const RAYMAX: i32 = 600;        // fallback ray march length
const HALF: f32 = 0.5 / 255.0;
const FLOOR_BIAS: f32 = 0.499 / 255.0;   // just under half a level: exact integers stay put

var<private> gHoriz: bool;
var<private> gDims: vec2<i32>;

fn wOf(m: f32, W: f32) -> f32 {
    return clamp(W - 1.43 / pow(max(m, 1e-3), 1.19), 0.0, 1.0);
}

fn op(s: vec4<f32>, v: vec4<f32>, w: f32, mode: i32) -> vec4<f32> {
    let mx = mix(s, v, w);
    if (mode == 2) {
        return select(s, mx, mx.r + mx.g + mx.b > s.r + s.g + s.b);
    }
    if (mode == 3) {
        return select(s, mx, mx.r + mx.g + mx.b < s.r + s.g + s.b);
    }
    return mx;
}

// pixel (major, minor) -> texel, 0 outside the layer
fn ld(major: i32, minor: i32, orig: bool) -> vec4<f32> {
    var c = vec2<i32>(major, minor);
    if (!gHoriz) {
        c = vec2<i32>(minor, major);
    }
    if (c.x < 0 || c.y < 0 || c.x >= gDims.x || c.y >= gDims.y) {
        return vec4<f32>(0.0);
    }
    if (orig) {
        return textureLoad(origTex, c, 0);
    }
    let v = textureLoad(srcTex, c, 0);
    return select(v, min(v + vec4<f32>(HALF), vec4<f32>(1.0)), v.a > 0.0);   // stored floor(): re-centre
}

// bilinear sample of the content at pixel-index coordinates q, transparent outside the layer
fn tapO(q: vec2<f32>, size: vec2<f32>) -> vec4<f32> {
    let o = max(-q, q - (size - vec2<f32>(1.0)));
    let cov = clamp(vec2<f32>(1.0) - o, vec2<f32>(0.0), vec2<f32>(1.0));
    let c = cov.x * cov.y;
    var v = vec4<f32>(0.0);
    if (c > 0.0) {
        v = c * textureSampleLevel(origTex, srcSamp, (q + vec2<f32>(0.5)) / size, 0.0);
    }
    return v;
}

fn minorMap(y: f32, cb: f32, t: f32) -> f32 {
    return cb + (y + 0.5 - cb) * t - 0.5;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    gDims = vec2<i32>(textureDimensions(origTex));
    let size = vec2<f32>(gDims);
    let pi = vec2<i32>(floor(in.uv * size));
    let keep = textureSampleLevel(srcTex, srcSamp, vec2<f32>(0.5), 0.0) * 0.0;
    let prevOut = textureLoad(srcTex, pi, 0) + keep;
    let c = P.center;
    let d = vec2<f32>(pi) + vec2<f32>(0.5) - c;
    let m = max(abs(d.x), abs(d.y));
    let n = i32(floor(m));
    let a = clamp(P.amount, 0.0, 100.0) / 100.0;
    var W = 0.0;
    if (a > 0.0) {
        W = exp(-0.1003 * pow(-log(a), 0.976));
    }
    let mode = i32(round(P.zoom));
    // farthest ring of the layer
    let far = max(max(abs(0.5 - c.x), abs(size.x - 0.5 - c.x)), max(abs(0.5 - c.y), abs(size.y - 0.5 - c.y)));
    let R = i32(floor(far));
    let K = max(1, (R + NPASS) / NPASS);
    let ip = i32(P.passIndex + 0.5);
    gHoriz = abs(d.x) >= abs(d.y);
    var pa = pi.x;
    var pb = pi.y;
    var ca = c.x;
    var cb = c.y;
    var da = d.x;
    if (!gHoriz) {
        pa = pi.y;
        pb = pi.x;
        ca = c.y;
        cb = c.x;
        da = d.y;
    }
    let sa = select(-1, 1, da >= 0.0);
    var result: vec4<f32>;

    if (K > KMAX) {
        // fallback: whole recursion along the ray in pass 0 (no sideways interpolation), copy afterwards
        if (ip != 0) {
            return prevOut;
        }
        let u = d / max(m, 1e-6);
        let j0 = min(n, RAYMAX);
        let p0 = vec2<f32>(pi);
        var v = tapO(p0 - f32(j0) * u, size);
        for (var j = j0 - 1; j >= 0; j--) {
            let q = p0 - f32(j) * u;                      // pixel-index coords
            v = op(tapO(q, size), v, wOf(m - f32(j), W), mode);
        }
        result = select(textureLoad(origTex, pi, 0), v, n > 0);
    } else {
        let lo = ip * K;
        if (n < lo || n >= lo + K) {
            if (ip == 0) {
                return textureLoad(origTex, pi, 0) + keep;  // not processed yet: the content
            }
            return prevOut;
        }
        if (n == 0) {
            result = textureLoad(origTex, pi, 0);
        } else {
            // cone depth: down to ring lo - 1 - margin (base rows final), or to the centre ring (= the layer)
            var Kb = n;
            var baseOrig = true;
            if (lo > 0) {
                let want = (n - lo) + 1 + K / 2 + 3;
                if (want < n) {
                    Kb = want;
                    baseOrig = false;
                }
            }
            Kb = min(Kb, LMAX - 1);
            // row ranges of the cone, level 0 (the pixel) to Kb
            var los: array<i32, 28>;
            var his: array<i32, 28>;
            los[0] = pb;
            his[0] = pb;
            for (var k = 0; k < Kb; k++) {
                let mk = m - f32(k);
                let t = (mk - 1.0) / mk;
                let ln = i32(floor(minorMap(f32(los[k]), cb, t)));
                los[k + 1] = ln;
                his[k + 1] = min(i32(floor(minorMap(f32(his[k]), cb, t))) + 1, ln + LMAX - 1);
            }
            var A: array<vec4<f32>, 28>;
            var B: array<vec4<f32>, 28>;
            let colB = pa - sa * Kb;
            let lb = los[Kb];
            let hb = his[Kb];
            for (var r = lb; r <= hb; r++) {
                A[r - lb] = ld(colB, r, baseOrig);
            }
            for (var k = Kb - 1; k >= 0; k--) {
                let mk = m - f32(k);
                let t = (mk - 1.0) / mk;
                let w = wOf(mk, W);
                let col = pa - sa * k;
                let l1 = los[k + 1];
                let h1 = his[k + 1];
                let l0 = los[k];
                let h0 = his[k];
                for (var r = l0; r <= h0; r++) {
                    let y = minorMap(f32(r), cb, t);
                    let i0 = i32(floor(y));
                    let f = y - f32(i0);
                    let ia = clamp(i0 - l1, 0, h1 - l1);
                    let ib = clamp(i0 + 1 - l1, 0, h1 - l1);
                    let v = mix(A[ia], A[ib], f);
                    let s = ld(col, r, true);
                    var o = op(s, v, w, mode);
                    var dimMinor = gDims.x;
                    var dimMajor = gDims.y;
                    if (gHoriz) {
                        dimMinor = gDims.y;
                        dimMajor = gDims.x;
                    }
                    let inMinor = (r >= 0) && (r < dimMinor);
                    let inMajor = (col >= 0) && (col < dimMajor);
                    let inside = inMinor && inMajor;
                    if (!inside) {
                        o = vec4<f32>(0.0);
                    }
                    B[r - l0] = o;
                }
                for (var r = 0; r <= h0 - l0; r++) {
                    A[r] = B[r];
                }
            }
            result = A[0];
        }
    }
    let al = clamp(result.a, 0.0, 1.0);
    let o = vec4<f32>(clamp(result.rgb, vec3<f32>(0.0), vec3<f32>(al)), al);
    return max(o - vec4<f32>(FLOOR_BIAS), vec4<f32>(0.0)) + keep;   // RGBA8 store rounds: -0.5 level = floor()
}
