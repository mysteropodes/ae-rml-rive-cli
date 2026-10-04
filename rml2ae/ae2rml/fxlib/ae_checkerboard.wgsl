// After Effects "Checkerboard" (ADBE Checkerboard, FR "Damier") — measured on AE 2026 8 bpc.
//   * the pattern is evaluated with pixel CENTRES at integer layer coordinates (pixel i covers [i - 0.5, i + 0.5]):
//     a check boundary through the Anchor at x = 320 gives pixel 320 half coverage (measured);
//   * check (ix, iy) = (floor((x - Anchor.x) / W), floor((y - Anchor.y) / H)) is filled when ix + iy is even;
//   * anti-aliasing = exact box-filter coverage of the pixel (separable: coverage = (1 + <u><v>) / 2, <u> the mean of
//     the +-1 square wave over the pixel); Feather Width / Height widen that box by the feather (unmeasured model);
//   * Size From: 1 Corner Point (W, H = |Corner - Anchor|), 2 Width Slider (W = H = Width), 3 Width & Height Sliders;
//   * alpha8 = floor(coverage * Opacity * 255) (AE truncates: 0.5 -> 127, 0.25 -> 63), colour = (C8 * a8 + 127) / 255;
//   * Blending Mode 1 None (default): only the checkerboard is rendered, the layer content is discarded (checks also
//     cover the layer's transparent pixels); 2..18 = the checks composited over the layer with the usual blend
//     formulas, 19 Stencil Alpha, 20 Silhouette Alpha, 21 Alpha Add, 22 Luminescent Premul (menu order assumed from
//     AE's 4-Color Gradient; AE lists 23 entries, the 23rd is treated as Normal) — modes 2..23 not measured.
struct Params {
    size: vec2<f32>,
    anchor: vec2<f32>,    // AE 1 Anchor (layer px)
    corner: vec2<f32>,    // AE 3 Corner (layer px, used by Size From = Corner Point)
    sizeFrom: f32,        // AE 2 Size From 1..3 (1 Corner Point, 2 Width Slider, 3 Width & Height Sliders)
    width: f32,           // AE 4 Width 1..4000 px
    height: f32,          // AE 5 Height 1..4000 px
    featherW: f32,        // AE 7 Feather Width 0..400 px
    featherH: f32,        // AE 8 Feather Height 0..400 px
    pad0: f32,
    color: vec4<f32>,     // AE 10 Color (straight 0..1)
    opacity: f32,         // AE 11 Opacity 0..100 %
    blendMode: f32,       // AE 12 Blending Mode 1..23 (1 None)
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

// integral of the +-1 square wave of period 2w (+1 on [0, w)): a triangle wave between 0 and w
fn sqInt(x: f32, w: f32) -> f32 {
    let s = x / w;
    let m = s - 2.0 * floor(s * 0.5);                       // 0..2
    return w * (1.0 - abs(m - 1.0));
}

// mean of the square wave over [x - h, x + h]
fn sqMean(x: f32, w: f32, h: f32) -> f32 {
    return (sqInt(x + h, w) - sqInt(x - h, w)) / (2.0 * h);
}

fn lum(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.3, 0.59, 0.11));
}

fn clipColor(c: vec3<f32>) -> vec3<f32> {
    let l = lum(c);
    let n = min(c.r, min(c.g, c.b));
    let x = max(c.r, max(c.g, c.b));
    var o = c;
    if (n < 0.0) {
        o = l + (o - l) * l / max(l - n, 1e-6);
    }
    if (x > 1.0) {
        o = l + (o - l) * (1.0 - l) / max(x - l, 1e-6);
    }
    return o;
}

fn setLum(c: vec3<f32>, l: f32) -> vec3<f32> {
    return clipColor(c + (l - lum(c)));
}

fn sat(c: vec3<f32>) -> f32 {
    return max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b));
}

fn setSat(c: vec3<f32>, s: f32) -> vec3<f32> {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    if (mx <= mn) {
        return vec3<f32>(0.0);
    }
    return (c - mn) * s / (mx - mn);
}

fn softLightCh(a: f32, b: f32) -> f32 {
    if (b <= 0.5) {
        return a - (1.0 - 2.0 * b) * a * (1.0 - a);
    }
    var d: f32;
    if (a <= 0.25) {
        d = ((16.0 * a - 12.0) * a + 4.0) * a;
    } else {
        d = sqrt(a);
    }
    return a + (2.0 * b - 1.0) * (d - a);
}

fn dodgeCh(a: f32, b: f32) -> f32 {
    if (a <= 0.0) {
        return 0.0;
    }
    if (b >= 1.0) {
        return 1.0;
    }
    return min(1.0, a / (1.0 - b));
}

fn burnCh(a: f32, b: f32) -> f32 {
    if (a >= 1.0) {
        return 1.0;
    }
    if (b <= 0.0) {
        return 0.0;
    }
    return 1.0 - min(1.0, (1.0 - a) / b);
}

// a = original (base, straight), b = gradient (straight)
fn blendRGB(mode: i32, a: vec3<f32>, b: vec3<f32>) -> vec3<f32> {
    var r = b;
    switch mode {
        case 3: { r = min(a + b, vec3<f32>(1.0)); }
        case 4: { r = a * b; }
        case 5: { r = 1.0 - (1.0 - a) * (1.0 - b); }
        case 6: { r = select(1.0 - 2.0 * (1.0 - a) * (1.0 - b), 2.0 * a * b, a < vec3<f32>(0.5)); }
        case 7: { r = vec3<f32>(softLightCh(a.r, b.r), softLightCh(a.g, b.g), softLightCh(a.b, b.b)); }
        case 8: { r = select(1.0 - 2.0 * (1.0 - a) * (1.0 - b), 2.0 * a * b, b < vec3<f32>(0.5)); }
        case 9: { r = vec3<f32>(dodgeCh(a.r, b.r), dodgeCh(a.g, b.g), dodgeCh(a.b, b.b)); }
        case 10: { r = vec3<f32>(burnCh(a.r, b.r), burnCh(a.g, b.g), burnCh(a.b, b.b)); }
        case 11: { r = min(a, b); }
        case 12: { r = max(a, b); }
        case 13: { r = abs(a - b); }
        case 14: { r = a + b - 2.0 * a * b; }
        case 15: { r = setLum(setSat(b, sat(a)), lum(a)); }
        case 16: { r = setLum(setSat(a, sat(b)), lum(a)); }
        case 17: { r = setLum(b, lum(a)); }
        case 18: { r = setLum(a, lum(b)); }
        default: { r = b; }
    }
    return r;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0);  // premultiplied layer content (canvas = layer size)
    let q = in.pos.xy - vec2<f32>(0.5) - P.anchor;           // pixel centre at integer layer coordinates
    let mode = i32(round(P.sizeFrom));
    var w = max(P.width, 1e-3);
    var h = w;
    if (mode == 1) {
        w = max(abs(P.corner.x - P.anchor.x), 1e-3);
        h = max(abs(P.corner.y - P.anchor.y), 1e-3);
    } else if (mode == 3) {
        h = max(P.height, 1e-3);
    }
    let hx = 0.5 + 0.5 * max(P.featherW, 0.0);
    let hy = 0.5 + 0.5 * max(P.featherH, 0.0);
    let cov = clamp(0.5 + 0.5 * sqMean(q.x, w, hx) * sqMean(q.y, h, hy), 0.0, 1.0);
    let op = clamp(P.opacity, 0.0, 100.0) / 100.0;
    let a8 = floor(cov * op * 255.0 + 0.01);                 // + f32 noise margin of the coverage
    let c8 = floor(clamp(P.color.rgb, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5);
    let bm = i32(round(P.blendMode));
    if (bm <= 1) {
        return vec4<f32>(floor((c8 * a8 + 127.0) / 255.0) / 255.0, a8 / 255.0);
    }
    let as_ = a8 / 255.0;
    let cs = c8 / 255.0;
    let ab = s.a;
    var cb = vec3<f32>(0.0);
    if (ab > 0.0) {
        cb = clamp(s.rgb / ab, vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (bm == 19) {                                          // Stencil Alpha: the layer seen through the checks
        return s * as_;
    }
    if (bm == 20) {                                          // Silhouette Alpha: the layer cut by the checks
        return s * (1.0 - as_);
    }
    let ao = as_ + ab * (1.0 - as_);
    if (bm == 22) {                                          // Luminescent Premul: premultiplied checks added
        return vec4<f32>(min(s.rgb + cs * as_, vec3<f32>(ao)), ao);
    }
    var bl = cs;
    if (bm >= 3 && bm <= 18) {
        bl = clamp(blendRGB(bm, cb, cs), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    let pm = as_ * (1.0 - ab) * cs + as_ * ab * bl + (1.0 - as_) * ab * cb;
    if (bm == 21) {                                          // Alpha Add: alphas added
        let aa = min(1.0, as_ + ab);
        return vec4<f32>(pm / max(ao, 1e-6) * aa, aa);
    }
    return vec4<f32>(pm, ao);
}
