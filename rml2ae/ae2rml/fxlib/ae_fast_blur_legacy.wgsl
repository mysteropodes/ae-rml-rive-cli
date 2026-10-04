// After Effects "Fast Blur (Legacy)" (ADBE Fast Blur, FR "Flou accéléré (hérité)") — measured on AE 2026 8 bpc
// (step-response + whole-image least-squares fits on the AE refs):
//   * per axis, THREE iterated box blurs on the PREMULTIPLIED colour; each box has a fractional radius
//     rho = 0.369 * Blurriness px: weight 1 on |x| <= floor(rho), weight frac(rho) on |x| = floor(rho) + 1
//     (total width 2 rho + 1). Horizontal first, then vertical.
//   * Repeat Edge Pixels OFF: the blur runs on an unbounded transparent plane (AE grows the layer buffer, the
//     intermediate box passes are NOT cropped to the layer), the result is then seen through the layer/comp.
//   * Repeat Edge Pixels ON: the layer's edge pixels are repeated outside (clamp-to-edge source, extended once
//     before the blur; not covered by the visible refs, so AE's exact edge semantics are unverified).
//   AE rounds to 8 bits after each box; here the rounding happens once per axis (RGBA8 intermediate): <= 2 LSB.
// Implementation: 2 passes (pass 0 horizontal, pass 1 vertical); each pass applies the exact composite kernel
// K = b * b * b of the three fractional boxes in one go (so nothing is cropped between the three boxes). K is built
// by walking its third difference, which is 16 impulses: (f + g z - g z^N - f z^(N+1))^3, N = 2 floor(rho) + 2,
// f = frac(rho), g = 1 - f. Taps per pass = 6 floor(rho) + 7: direct taps up to 300 (Blurriness <= ~130),
// exact bilinear pairs up to 600 (<= ~265), beyond that a stride-G coarse kernel (approximation).
struct Params {
    size: vec2<f32>,
    blurriness: f32,      // AE 1 Blurriness 0..30000 (px-ish units; box radius = 0.369 * Blurriness)
    dims: f32,            // AE 2 Blur Dimensions popup 1..3 (1 = Horizontal and Vertical, 2 = Horizontal, 3 = Vertical)
    repeatEdge: f32,      // AE 3 Repeat Edge Pixels checkbox 0/1 (AE default 1)
    passIndex: f32,
    pad0: f32,
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

const RHO_PER_BLURRINESS: f32 = 0.369;
const MAX_TAPS: i32 = 300;

// one texel of the source along the blur axis (index t, integer), other coordinate fixed; transparent outside
// unless Repeat Edge Pixels (then clamped = the sampler's clamp-to-edge)
fn texel(t: f32, other: f32, horiz: bool, L: f32, repeatEdge: bool) -> vec4<f32> {
    if (!repeatEdge && (t < 0.0 || t > L - 1.0)) {
        return vec4<f32>(0.0);
    }
    let a = (clamp(t, 0.0, L - 1.0) + 0.5) / L;
    let uv = select(vec2<f32>(other, a), vec2<f32>(a, other), horiz);
    return textureSampleLevel(srcTex, srcSamp, uv, 0.0);
}

// linear sample between texels floor(t) and floor(t) + 1 (t in texel-index units)
fn lerpSample(t: f32, other: f32, horiz: bool, L: f32, repeatEdge: bool) -> vec4<f32> {
    let k = floor(t);
    let a = t - k;
    if (a == 0.0) {
        return texel(k, other, horiz, L, repeatEdge);
    }
    if (repeatEdge || (k >= 0.0 && k + 1.0 <= L - 1.0)) {
        let c = (t + 0.5) / L;                      // hardware lerp (clamp-to-edge = repeat edge pixels)
        let uv = select(vec2<f32>(other, c), vec2<f32>(c, other), horiz);
        return textureSampleLevel(srcTex, srcSamp, uv, 0.0);
    }
    return texel(k, other, horiz, L, repeatEdge) * (1.0 - a) + texel(k + 1.0, other, horiz, L, repeatEdge) * a;
}

// third-difference impulse table of K = b*b*b, b = fractional box (f at both ends, 1 inside); index = h * 4 + l,
// impulse position s = h * N + l
fn impulses(f: f32) -> array<f32, 16> {
    var cf: array<f32, 16>;
    for (var i = 0; i < 16; i++) {
        cf[i] = 0.0;
    }
    let g = 1.0 - f;
    var c = array<f32, 4>(f, g, -g, -f);           // offsets 0, 1, N, N + 1
    for (var i = 0; i < 64; i++) {
        let e0 = i & 3;
        let e1 = (i >> 2u) & 3;
        let e2 = (i >> 4u) & 3;
        let h = (e0 >> 1u) + (e1 >> 1u) + (e2 >> 1u);
        let l = (e0 & 1) + (e1 & 1) + (e2 & 1);
        cf[h * 4 + l] += c[e0] * c[e1] * c[e2];
    }
    return cf;
}

fn impulseAt(cf: ptr<function, array<f32, 16>>, s: i32, N: i32) -> f32 {
    var imp = 0.0;
    for (var h = 0; h < 4; h++) {
        let l = s - h * N;
        if (l >= 0 && l <= 3) {
            imp += (*cf)[h * 4 + l];
        }
    }
    return imp;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let px = floor(in.uv * P.size);                 // this pixel's texel index
    let ctr = (px + 0.5) / P.size;
    let pi = i32(round(P.passIndex));
    let horiz = pi == 0;
    let dims = i32(round(P.dims));
    let isOn = select(dims != 2, dims != 3, horiz);   // 2 = horizontal only, 3 = vertical only
    let rhoFull = max(P.blurriness, 0.0) * RHO_PER_BLURRINESS;
    if (!isOn || rhoFull <= 0.0) {
        return textureSampleLevel(srcTex, srcSamp, ctr, 0.0);
    }
    let repeatEdge = P.repeatEdge > 0.5;
    let L = select(P.size.y, P.size.x, horiz);
    let p = select(px.y, px.x, horiz);
    let other = select(ctr.x, ctr.y, horiz);

    // exact taps count; beyond 2 * MAX_TAPS a coarse kernel (stride G, same total box width) is used
    let mFull = floor(rhoFull);
    let tapsFull = 6 * i32(mFull) + 7;
    var G = 1;
    var rho = rhoFull;
    if (tapsFull > 2 * MAX_TAPS) {
        G = (tapsFull + MAX_TAPS - 1) / MAX_TAPS;
        rho = max((2.0 * rhoFull + 1.0) / (2.0 * f32(G)) - 0.5, 0.0);
    }
    let m = floor(rho);
    let f = rho - m;
    let N = 2 * i32(m) + 2;
    let taps = 3 * N + 1;
    let halfW = 3.0 * (m + 1.0);                       // kernel offsets j = s - half, s = 0 .. 3N
    var cf = impulses(f);

    var acc = vec4<f32>(0.0);
    var wsum = 0.0;
    var d2 = 0.0;
    var d1 = 0.0;
    var d0 = 0.0;
    if (tapsFull <= MAX_TAPS) {
        // direct taps
        for (var s = 0; s < taps; s++) {
            d2 += impulseAt(&cf, s, N);
            d1 += d2;
            d0 += d1;
            if (d0 > 0.0) {
                acc += texel(p + f32(s) - halfW, other, horiz, L, repeatEdge) * d0;
            }
            wsum += max(d0, 0.0);
        }
    } else if (G == 1) {
        // exact bilinear pairs (s, s + 1): one fetch at the weighted position
        for (var s = 0; s < taps; s += 2) {
            d2 += impulseAt(&cf, s, N);
            d1 += d2;
            d0 += d1;
            let w0 = max(d0, 0.0);
            var w1 = 0.0;
            if (s + 1 < taps) {
                d2 += impulseAt(&cf, s + 1, N);
                d1 += d2;
                d0 += d1;
                w1 = max(d0, 0.0);
            }
            let w = w0 + w1;
            if (w > 0.0) {
                acc += lerpSample(p + f32(s) - halfW + w1 / w, other, horiz, L, repeatEdge) * w;
            }
            wsum += w;
        }
    } else {
        // coarse kernel: one (bilinear) fetch every G texels — an approximation for very large Blurriness
        let gf = f32(G);
        for (var s = 0; s < taps; s++) {
            d2 += impulseAt(&cf, s, N);
            d1 += d2;
            d0 += d1;
            if (d0 > 0.0) {
                acc += lerpSample(p + (f32(s) - halfW) * gf, other, horiz, L, repeatEdge) * d0;
            }
            wsum += max(d0, 0.0);
        }
    }
    return acc / max(wsum, 1e-20);
}
