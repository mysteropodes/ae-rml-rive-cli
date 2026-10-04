// After Effects "Directional Blur" (ADBE Motion Blur) — a symmetric blur along one direction, on premultiplied RGBA.
// Measured against AE 2026 8 bpc (fxref directional_blur_0/1, max error 1 level):
//   * Direction θ in degrees from up, clockwise: the blur axis is (sin θ, −cos θ) (45° = lower-left <-> upper-right);
//   * the taps step ONE PIXEL along the major axis: step = dir / max(|sin θ|, |cos θ|) (45° -> exact (1, −1) steps,
//     no resampling); the minor-axis coordinate is interpolated linearly (bilinear sampler — only exercised by
//     angles that are not multiples of 45°, unverified);
//   * the kernel along the line is three successive box blurs (B³, B = taps −n..n weight 1, ±(n+1) weight f) of
//     FRACTIONAL radius r = n + f = 0.369 · Blur Length · max(|sin θ|, |cos θ|) taps, i.e. 0.369 · Blur Length px
//     along the direction whatever the angle (fits: 20 px at 0° -> r = 7.38, 30 px at 45° -> r = 7.83 taps);
//   * outside the layer is transparent (no edge repeat).
// Single pass: the B³ kernel is evaluated in closed form (no RGBA8 intermediate). Above MAX_TAPS taps
// (Blur Length ≳ 110 px at 0°) the taps are strided and renormalised (approximation of very long blurs).
struct Params {
    size: vec2<f32>,
    direction: f32,       // AE 1 Direction degrees (0 = vertical, clockwise from up; default 90 = horizontal)
    blurLength: f32,      // AE 2 Blur Length px 0..1000 (default 10)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
    pad2: f32,
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

const MAX_TAPS: i32 = 255;
const RADIUS_PER_LENGTH: f32 = 0.369;

fn c2(x: f32) -> f32 {                       // binomial(x, 2), 0 below 2
    return select(0.0, x * (x - 1.0) * 0.5, x >= 2.0);
}

// three convolved boxes of radius r = n + f (unnormalised; divide by (2n+1+2f)^3)
fn k3(j: f32, n: f32, f: f32) -> f32 {
    let m = 2.0 * n + 1.0;
    let q = n + 1.0;
    let aj = abs(j);
    let t = j + 3.0 * n + 2.0;
    var v = c2(t) - 3.0 * c2(t - m) + 3.0 * c2(t - 2.0 * m) - c2(t - 3.0 * m);
    v += 3.0 * f * (max(0.0, m - abs(j + q)) + max(0.0, m - abs(j - q)));
    let u0 = select(0.0, 1.0, aj <= n);
    let um = select(0.0, 1.0, abs(j + 2.0 * q) <= n);
    let up = select(0.0, 1.0, abs(j - 2.0 * q) <= n);
    v += 3.0 * f * f * (um + 2.0 * u0 + up);
    let e3 = select(0.0, 1.0, aj == 3.0 * q) + 3.0 * select(0.0, 1.0, aj == q);
    v += f * f * f * e3;
    return v;
}

// bilinear sample at pixel-centre coordinates q (pixel i centre = i), transparent outside the layer:
// the clamp-to-edge sampler is corrected by the in-layer fraction of the bilinear footprint on each axis
fn tap(q: vec2<f32>, size: vec2<f32>) -> vec4<f32> {
    let o = max(-q, q - (size - vec2<f32>(1.0)));
    let cov = clamp(vec2<f32>(1.0) - o, vec2<f32>(0.0), vec2<f32>(1.0));
    let c = cov.x * cov.y;
    if (c <= 0.0) {
        return vec4<f32>(0.0);
    }
    return c * textureSampleLevel(srcTex, srcSamp, (q + vec2<f32>(0.5)) / size, 0.0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let size = vec2<f32>(textureDimensions(srcTex));
    let p = floor(in.uv * size);                 // pixel index (centre coordinates)
    let th = radians(P.direction);
    let dir = vec2<f32>(sin(th), -cos(th));
    let major = max(abs(dir.x), abs(dir.y));
    var stp = dir / major;                        // one pixel per tap along the major axis
    stp = select(stp, round(stp), abs(stp - round(stp)) < vec2<f32>(1e-4));
    let r = max(P.blurLength, 0.0) * RADIUS_PER_LENGTH * major;
    let n = floor(r);
    let f = r - n;
    let R = i32(3.0 * n + select(0.0, 3.0, f > 0.0));
    let span = 2 * R + 1;
    let stride = max(1, (span + MAX_TAPS - 1) / MAX_TAPS);
    var acc = vec4<f32>(0.0);
    var wsum = 0.0;
    if (stride == 1) {
        for (var j = -R; j <= R; j++) {
            let fj = f32(j);
            let w = k3(fj, n, f);
            acc += w * tap(p + fj * stp, size);
        }
        acc /= pow(2.0 * n + 1.0 + 2.0 * f, 3.0);
    } else {
        // long blur: one (bilinear) sample per stride cell, at the cell centre, weighted by the kernel there
        let half = 0.5 * f32(stride - 1);
        for (var j = -R; j <= R; j += stride) {
            let fj = f32(j) + half;
            let w = k3(round(fj), n, f);
            acc += w * tap(p + fj * stp, size);
            wsum += w;
        }
        if (wsum > 0.0) {
            acc /= wsum;
        }
    }
    let a = clamp(acc.a, 0.0, 1.0);
    return vec4<f32>(clamp(acc.rgb, vec3<f32>(0.0), vec3<f32>(a)), a);
}
