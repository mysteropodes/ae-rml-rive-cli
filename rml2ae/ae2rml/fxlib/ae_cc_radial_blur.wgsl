// After Effects "CC Radial Blur" (CC Radial Blur) — zoom / rotation blur around Center, on premultiplied RGBA.
// Type popup (read from the plug-in's resources): 1 Straight Zoom, 2 Fading Zoom, 3 Centered Zoom, 4 "(-" separator,
// 5 Rotate, 6 Scratch, 7 Rotate Fading.
// Measured against AE 2026 8 bpc (fxref cc_radial_blur_0 = Straight Zoom 30, _1 = Type 4, _2 = Scratch 40):
//   * the blur spans Amount / 200 of the full range: Straight Zoom averages the layer at the scales
//     s in [1 - Amount/200, 1] around Center (one-sided, towards Center: streaks grow outwards); Scratch averages the
//     rotations in [-a, +a] with 2a = Amount/200 turn (Amount 40 -> +-36 deg; residual ~0.4 % mean near Center:
//     AE's Scratch sampling has a finer pixel-level moire this model does not reproduce);
//   * the samples are a FINITE set, evenly spaced, whose count grows with the streak length at the pixel:
//     n = ceil(L / 1.9) for Quality 50 (L = streak length in px: Amount/200 * r for the zoom, 2a * r for the
//     rotation, r = distance to Center); zoom: n samples at i/n of the range (i = 0..n-1), rotation: n + 1 samples
//     including both ends — hence the visible ghosting near Center; bilinear, transparent outside the layer;
//   * the result is truncated (floor) to 8 bits;
//   * Type 4 is the separator line of the popup: AE renders nothing (transparent) — reproduced.
//   * 3 Centered Zoom (fitted on the held-out cc_radial_blur_3, Amount 25, which is therefore no longer an
//     independent check): uniform over the scales [1 - Amount/400, 1 + Amount/400] (nonparametric kernel fit), with
//     the same n = ceil(L / 1.9) samples at t = (i + 1) / n, i.e. the inner end included, the outer end excluded
//     (the other placements leave 9-30 % of pixels off by more than 8 levels);
// NOT measured (inferred, unverified): 2 Fading Zoom (= 1 with weights fading linearly to 0 at the far end),
// 5 Rotate (= the Scratch model),
// 7 Rotate Fading (one-sided rotation [0, Amount/200 turn], weights fading to 0), negative Amounts (mirrored range), Quality (taken as samples
// proportional to Quality: spacing 1.9 * 50 / Quality px). More than MAXN samples are spread evenly (approximation).
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 4 Center (point, layer px)
    kind: f32,            // AE 1 Type popup 1..7 (1 Straight Zoom, 2 Fading Zoom, 3 Centered, 4 separator, 5..7 rotations)
    amount: f32,          // AE 2 Amount -250..250 (default 0)
    quality: f32,         // AE 3 Quality 10..100 (default 50)
    passIndex: f32,
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

const MAXN: i32 = 512;
const SPACING: f32 = 1.9;                  // px between samples at Quality 50 (measured)
const FLOOR_BIAS: f32 = 0.499 / 255.0;      // RGBA8 store rounds: -0.5 level = floor()
const TAU: f32 = 6.283185307;

// bilinear sample at layer px q (pixel i centre = i + 0.5), transparent outside the layer
fn tap(q: vec2<f32>, size: vec2<f32>) -> vec4<f32> {
    let p = q - vec2<f32>(0.5);
    let o = max(-p, p - (size - vec2<f32>(1.0)));
    let cov = clamp(vec2<f32>(1.0) - o, vec2<f32>(0.0), vec2<f32>(1.0));
    let c = cov.x * cov.y;
    var v = vec4<f32>(0.0);
    if (c > 0.0) {
        v = c * textureSampleLevel(srcTex, srcSamp, q / size, 0.0);
    }
    return v;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let size = vec2<f32>(textureDimensions(srcTex));
    let pc = floor(in.uv * size) + vec2<f32>(0.5);
    let kind = i32(round(P.kind));
    if (kind == 4) {
        return vec4<f32>(0.0);
    }
    let d = pc - P.center;
    let r = length(d);
    let frac = P.amount / 200.0;                       // signed share of the full range
    let rotate = kind >= 5;
    let fading = kind == 2 || kind == 7;
    var lo = 0.0;                                      // range in "units": scale offset or turns
    var hi = frac;
    if (kind == 3 || kind == 5 || kind == 6) {
        lo = -0.5 * frac;
        hi = 0.5 * frac;
    }
    var L = abs(hi - lo) * r;                          // streak length in px
    if (rotate) {
        L = L * TAU;
    }
    let spacing = SPACING * 50.0 / clamp(P.quality, 10.0, 100.0);
    let n = clamp(i32(ceil(L / spacing - 1e-4)), 1, MAXN);
    let incl = rotate;                                 // rotations include both ends (measured on Type 6)
    var cnt = n;
    if (incl) {
        cnt = n + 1;
    }
    var acc = vec4<f32>(0.0);
    var wsum = 0.0;
    for (var i = 0; i < cnt; i++) {
        var t = f32(i) / f32(n);                       // 0 .. 1 (exclusive end for the zooms)
        if (kind == 3) {
            t = f32(i + 1) / f32(n);                   // Centered Zoom: the outer end (scale 1 + f/2) is the excluded one
        }
        let u = mix(lo, hi, t);
        var q: vec2<f32>;
        if (rotate) {
            let a = u * TAU;
            let cs = cos(a);
            let sn = sin(a);
            q = P.center + vec2<f32>(d.x * cs - d.y * sn, d.x * sn + d.y * cs);
        } else {
            q = P.center + d * (1.0 - u);
        }
        var w = 1.0;
        if (fading) {
            w = 1.0 - t;
        }
        acc += w * tap(q, size);
        wsum += w;
    }
    let o = acc / max(wsum, 1e-6);
    let al = clamp(o.a, 0.0, 1.0);
    let res = vec4<f32>(clamp(o.rgb, vec3<f32>(0.0), vec3<f32>(al)), al);
    return max(res - vec4<f32>(FLOOR_BIAS), vec4<f32>(0.0));
}
