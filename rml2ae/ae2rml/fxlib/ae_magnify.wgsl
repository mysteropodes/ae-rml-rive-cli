// After Effects "Magnify" (ADBE Magnify) — a circular / square lens showing the layer scaled around Center.
// Measured against AE 2026 8 bpc (fxref magnify_0 circle, magnify_1 square + feather):
//   * inside the lens, pixel centre p (layer px, +0.5) shows the layer at Center + (p - Center) * 100 / Magnification,
//     bilinear on the premultiplied layer (bit-exact), transparent outside the layer;
//   * the lens is Size px in radius (circle) or half-side (square); its mask is smoothstep over the signed distance
//     sd in [-h, +h], h = Feather + 0.72 px (one fit covers both Feather 0 — edge antialiasing — and Feather 10);
//     the square is a ROUNDED square: sd = |max(|e| - (Size - h), 0)| - h (measured on its feathered corner);
//   * Blending Mode 2 (default, "Normal"): result = mix(layer, magnified, mask * Opacity / 100).
// Link (AE 4) has no effect on a render (Size 80 / Magnification 300 / Link 2 renders an 80 px lens): it only couples
// the sliders in AE's UI. Blending Mode popup (AE dictionary): 1 None, 2 Normal, 3 (-, 4 Add, 5 Multiply, 6 Screen,
// 7 Overlay, 8 Soft Light, 9 Hard Light, 10 (-, 11 Color Dodge, 12 Color Burn, 13 (-, 14 Darken, 15 Lighten,
// 16 Difference, 17 Exclusion, 18 (-, 19 Hue, 20 Saturation, 21 Color, 22 Luminosity. Only Normal is measured; the
// others use the W3C formulas (same code as _ae_fx_mix.wgsl) composited lens-over-layer then faded by the mask, and
// None is taken as "the lens alone" (transparent around it) — unverified. Not reproduced: Scaling Soft / Scatter
// (AE 8; Standard = bilinear), Resize Layer.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,      // AE 2 Center (point, layer px)
    shape: f32,             // AE 1 Shape popup 1 Circle, 2 Square
    magnification: f32,     // AE 3 Magnification % (100..20000, default 150)
    link: f32,              // AE 4 Link popup (UI-only coupling, no effect here)
    lensSize: f32,          // AE 5 Size px (radius / half-side, default 100)
    feather: f32,           // AE 6 Feather px (default 0)
    opacity: f32,           // AE 7 Opacity % (default 100)
    scaling: f32,           // AE 8 Scaling popup 1 Standard, 2 Soft, 3 Scatter (only Standard reproduced)
    blendMode: f32,         // AE 9 Blending Mode popup (default 2 Normal)
    resizeLayer: f32,       // AE 10 Resize Layer checkbox (not reproduced)
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

const AA: f32 = 0.72;          // edge softness added to Feather (measured)

// bilinear sample at pixel-index coordinates q (pixel i centre = i), transparent outside the layer
fn tap(q: vec2<f32>, size: vec2<f32>) -> vec4<f32> {
    let o = max(-q, q - (size - vec2<f32>(1.0)));
    let cov = clamp(vec2<f32>(1.0) - o, vec2<f32>(0.0), vec2<f32>(1.0));
    let c = cov.x * cov.y;
    var v = vec4<f32>(0.0);
    if (c > 0.0) {
        v = c * textureSampleLevel(srcTex, srcSamp, (q + vec2<f32>(0.5)) / size, 0.0);
    }
    return v;
}

fn lum(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.3, 0.59, 0.11));
}

fn clipColor(c: vec3<f32>) -> vec3<f32> {
    let l = lum(c);
    let n = min(min(c.r, c.g), c.b);
    let x = max(max(c.r, c.g), c.b);
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
    return max(max(c.r, c.g), c.b) - min(min(c.r, c.g), c.b);
}

fn setSat(c: vec3<f32>, s: f32) -> vec3<f32> {
    let mx = max(max(c.r, c.g), c.b);
    let mn = min(min(c.r, c.g), c.b);
    let d = mx - mn;
    if (d <= 1e-6) {
        return vec3<f32>(0.0);
    }
    return (c - mn) * s / d;
}

fn ch(b: f32, s: f32, m: i32) -> f32 {
    switch m {
        case 1: { return b * s; }
        case 2: { return b + s - b * s; }
        case 3: { if (b <= 0.5) { return 2.0 * b * s; } return 1.0 - 2.0 * (1.0 - b) * (1.0 - s); }
        case 4: { return min(b, s); }
        case 5: { return max(b, s); }
        case 6: { if (b <= 0.0) { return 0.0; } if (s >= 1.0) { return 1.0; } return min(1.0, b / (1.0 - s)); }
        case 7: { if (b >= 1.0) { return 1.0; } if (s <= 0.0) { return 0.0; } return 1.0 - min(1.0, (1.0 - b) / s); }
        case 8: { if (s <= 0.5) { return 2.0 * b * s; } return 1.0 - 2.0 * (1.0 - b) * (1.0 - s); }
        case 9: {
            if (s <= 0.5) { return b - (1.0 - 2.0 * s) * b * (1.0 - b); }
            var d = sqrt(b);
            if (b <= 0.25) { d = ((16.0 * b - 12.0) * b + 4.0) * b; }
            return b + (2.0 * s - 1.0) * (d - b);
        }
        case 10: { return abs(b - s); }
        case 11: { return b + s - 2.0 * b * s; }
        case 12: { return min(1.0, b + s); }
        case 13: { return max(0.0, b + s - 1.0); }
        case 14: { return max(0.0, b - s); }
        case 19: { return clamp(b + 2.0 * s - 1.0, 0.0, 1.0); }
        case 20: {
            if (s <= 0.5) { if (s <= 0.0) { return 0.0; } return 1.0 - min(1.0, (1.0 - b) / (2.0 * s)); }
            if (s >= 1.0) { return 1.0; }
            return min(1.0, b / (2.0 * (1.0 - s)));
        }
        case 21: { if (s <= 0.5) { return min(b, 2.0 * s); } return max(b, 2.0 * s - 1.0); }
        case 22: { if (b + s >= 1.0) { return 1.0; } return 0.0; }
        case 25: { if (s <= 0.0) { return 1.0; } return min(1.0, b / s); }
        default: { return s; }
    }
}

fn blendf(b: vec3<f32>, s: vec3<f32>, m: i32) -> vec3<f32> {
    if (m == 15) { return setLum(setSat(s, sat(b)), lum(b)); }
    if (m == 16) { return setLum(setSat(b, sat(s)), lum(b)); }
    if (m == 17) { return setLum(s, lum(b)); }
    if (m == 18) { return setLum(b, lum(s)); }
    if (m == 23) { if (lum(s) > lum(b)) { return s; } return b; }
    if (m == 24) { if (lum(s) < lum(b)) { return s; } return b; }
    return vec3<f32>(ch(b.r, s.r, m), ch(b.g, s.g, m), ch(b.b, s.b, m));
}

// Magnify Blending Mode popup index -> W3C mode code of blendf (0 = Normal)
fn modeCode(i: i32) -> i32 {
    switch i {
        case 4: { return 12; }
        case 5: { return 1; }
        case 6: { return 2; }
        case 7: { return 3; }
        case 8: { return 9; }
        case 9: { return 8; }
        case 11: { return 6; }
        case 12: { return 7; }
        case 14: { return 4; }
        case 15: { return 5; }
        case 16: { return 10; }
        case 17: { return 11; }
        case 19: { return 15; }
        case 20: { return 16; }
        case 21: { return 17; }
        case 22: { return 18; }
        default: { return 0; }
    }
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let size = vec2<f32>(textureDimensions(srcTex));
    let pi = floor(in.uv * size);
    let pc = pi + vec2<f32>(0.5);
    let base = textureLoad(srcTex, vec2<i32>(pi), 0);
    let e = pc - P.center;
    let S = max(P.lensSize, 0.0);
    let h = max(P.feather, 0.0) + AA;
    var sd: f32;
    if (i32(round(P.shape)) == 2) {
        let k = max(S - h, 0.0);
        sd = length(max(abs(e) - vec2<f32>(k), vec2<f32>(0.0))) - (S - k);
    } else {
        sd = length(e) - S;
    }
    let a = smoothstep(0.0, 1.0, (h - sd) / (2.0 * h)) * clamp(P.opacity / 100.0, 0.0, 1.0);
    let zoom = max(P.magnification, 1.0) / 100.0;
    var lens = vec4<f32>(0.0);
    if (a > 0.0) {
        lens = tap(P.center + e / zoom - vec2<f32>(0.5), size);
    }
    let bm = i32(round(P.blendMode));
    if (bm == 1) {
        return lens * a;
    }
    let m = modeCode(bm);
    if (m == 0 || a <= 0.0) {
        return mix(base, lens, a);
    }
    // W3C: lens (source) over the layer (backdrop) with the blend mode, then faded in by the mask
    let ab = base.a;
    let as_ = lens.a;
    var cb = vec3<f32>(0.0);
    if (ab > 0.0) { cb = base.rgb / ab; }
    var cs = vec3<f32>(0.0);
    if (as_ > 0.0) { cs = lens.rgb / as_; }
    let co = lens.rgb * (1.0 - ab) + base.rgb * (1.0 - as_) + as_ * ab * blendf(cb, cs, m);
    let ao = as_ + ab * (1.0 - as_);
    return mix(base, vec4<f32>(co, ao), a);
}
