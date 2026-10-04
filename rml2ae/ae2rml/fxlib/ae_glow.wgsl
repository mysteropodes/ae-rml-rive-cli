// After Effects "Glow" (ADBE Glo2, FR "Lueur") — measured against AE 2026 8 bpc (Radius 20 / 40 / 25, Threshold
// 153 / 100, Intensity 1 / 2, Original Colors and A & B Colors, Composite Original Behind, Glow Operation Add):
//   * threshold = a SOFT linear ramp of width W = 30.6 levels (12 % of 255) centred on Threshold (0..255 raw):
//     t(x) = clamp((x - Threshold) / 30.6 + 0.5, 0, 1) (free-form fits give a straight ramp 137..168 at 153 and
//     85..116 at 100: the width does not scale with the threshold);
//   * Original Colors (Glow Based On Color Channels): each STRAIGHT channel is thresholded on its own and
//     premultiplied, s_c = t(c) * alpha; the glow alpha is the blurred layer alpha s_a = alpha (not thresholded:
//     black opaque areas glow in alpha only);
//   * A & B Colors: one glow value v from the Rec.601 luminance of the PREMULTIPLIED colour, s = t(0.299 R + 0.587 G
//     + 0.114 B) (Rec.709 / mean / straight luminance fit clearly worse); the glow is colour(v) * v with alpha v;
//     Triangle A>B>A, 1 cycle, phase 0, midpoint 0.5 measured as colour = mix(B, A, 1 - |2 v - 1|) (B where the glow
//     is faint or saturated, A at v = 0.5);
//   * blur = separable Gaussian, sigma = 0.404 * Radius, taps |x| <= ceil(Radius) - 1 (fits 0.4015..0.404 on all
//     refs; AE's weights are integers /256, residual <= 1 level on the alpha profiles), on a transparent plane;
//   * Intensity multiplies the blurred colour glow, which is then limited by the glow alpha:
//     glow = (min(I * blur(s_c), blur(s_a)), blur(s_a)) — near the layer edge an Intensity 2 glow saturates to the
//     alpha instead of doubling (measured), deep inside it doubles;
//   * Glow Operation Add + Composite Original Behind (measured, exact on half-transparent pixels too):
//     L = original + glow (premultiplied add, alpha = union), result = L over the original (the original is put
//     BEHIND the glow-combined layer, so semi-transparent originals count twice: alpha = 1 - (1-ao)^2 (1-ag)), then
//     colour clipped to the alpha.
// NOT covered by the refs (best guesses, see the manifest notes): Glow Based On Alpha Channel, Arbitrary Map, the
// other Color Looping modes / cycles / phase / midpoint, Intensity with A & B Colors, Composite On Top / None, the
// other Glow Operations (W3C formulas of _ae_fx_mix.wgsl through AE's separable blend rule), Glow Dimensions
// Horizontal / Vertical. Glow Operation menu assumed: 1 None, 2 Normal, 3 Add, 4 Multiply, 5 Screen, 6 Overlay,
// 7 Soft Light, 8 Hard Light, 9 Linear Light, 10 Vivid Light, 11 Pin Light, 12 Color Dodge, 13 Classic Color Dodge,
// 14 Linear Dodge, 15 Color Burn, 16 Classic Color Burn, 17 Linear Burn, 18 Darken, 19 Lighten, 20 Difference,
// 21 Classic Difference, 22 Exclusion, 23 Hue, 24 Saturation, 25 Color.
// 2 passes: pass 0 = horizontal blur of the glow sources (RGBA8: s_rgb, s_a; A & B: v as 16 bits in R,G); pass 1 =
// vertical blur + intensity + colour map + composite with origTex. Above MAX_TAPS taps per axis (Radius > ~256) the
// taps are strided (approximation). The glow spills outside the content: give the node an fxPad >= Radius when the
// content reaches the layer edge (AE's own behaviour at the layer edge is unmeasured).
struct Params {
    size: vec2<f32>,
    pad0: vec2<f32>,
    colorA: vec4<f32>,    // AE 12 Color A (straight 0..1, default white)
    colorB: vec4<f32>,    // AE 13 Color B (straight 0..1, default black)
    basedOn: f32,         // AE 1 Glow Based On popup 1 = Alpha Channel, 2 = Color Channels (default)
    threshold: f32,       // AE 2 Glow Threshold 0..255 raw (default 153 = 60 %)
    radius: f32,          // AE 3 Glow Radius px 0..1000 (default 10)
    intensity: f32,       // AE 4 Glow Intensity 0..255 (default 1)
    composite: f32,       // AE 5 Composite Original popup 1 = On Top, 2 = Behind (default), 3 = None
    operation: f32,       // AE 6 Glow Operation popup 1..25 (default 3 = Add)
    colors: f32,          // AE 7 Glow Colors popup 1 = Original Colors (default), 2 = A & B Colors, 3 = Arbitrary Map
    looping: f32,         // AE 8 Color Looping popup 1 = Sawtooth A>B, 2 = Sawtooth B>A, 3 = Triangle A>B>A (default), 4 = Triangle B>A>B
    cycles: f32,          // AE 9 Color Loops 1..127 (default 1)
    phase: f32,           // AE 10 Color Phase degrees (default 0)
    midpoint: f32,        // AE 11 A & B Midpoint 0.01..0.99 (default 0.5)
    dims: f32,            // AE 14 Glow Dimensions popup 1 = Horizontal and Vertical, 2 = Horizontal, 3 = Vertical
    passIndex: f32,
    pad1: f32,
    pad2: f32,
    pad3: f32,
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

const SIGMA_PER_RADIUS: f32 = 0.404;
const RAMP: f32 = 30.6;          // threshold ramp width, in 0..255 levels
const MAX_TAPS: i32 = 513;

fn ramp(x: f32) -> f32 {          // x in 0..255
    return clamp((x - P.threshold) / RAMP + 0.5, 0.0, 1.0);
}

fn abMode() -> bool {
    return i32(round(P.colors)) != 1;
}

// glow sources of content texel q (premultiplied input), transparent outside
fn sourceAt(q: vec2<i32>, dims: vec2<i32>) -> vec4<f32> {
    if (q.x < 0 || q.y < 0 || q.x >= dims.x || q.y >= dims.y) {
        return vec4<f32>(0.0);
    }
    let o = textureLoad(origTex, q, 0);
    let alphaBased = i32(round(P.basedOn)) == 1;
    if (abMode()) {
        if (alphaBased) {
            return vec4<f32>(ramp(o.a * 255.0) * o.a, 0.0, 0.0, 0.0);
        }
        return vec4<f32>(ramp(dot(o.rgb, vec3<f32>(0.299, 0.587, 0.114)) * 255.0), 0.0, 0.0, 0.0);
    }
    if (o.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    if (alphaBased) {
        let k = ramp(o.a * 255.0);
        return vec4<f32>(o.rgb * k, o.a * k);
    }
    let s = clamp(o.rgb / o.a, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0;
    return vec4<f32>(vec3<f32>(ramp(s.r), ramp(s.g), ramp(s.b)) * o.a, o.a);
}

// intermediate (pass 0 output) at texel q, decoded
fn midAt(q: vec2<i32>, dims: vec2<i32>) -> vec4<f32> {
    if (q.x < 0 || q.y < 0 || q.x >= dims.x || q.y >= dims.y) {
        return vec4<f32>(0.0);
    }
    let t = textureLoad(srcTex, q, 0);
    if (abMode()) {
        return vec4<f32>((round(t.r * 255.0) * 256.0 + round(t.g * 255.0)) / 65535.0, 0.0, 0.0, 0.0);
    }
    return t;
}

fn blur1(p: vec2<i32>, dir: vec2<i32>, dims: vec2<i32>, fromSource: bool) -> vec4<f32> {
    let R = max(P.radius, 0.0);
    let n = max(i32(ceil(R)) - 1, 0);
    if (n == 0) {
        if (fromSource) {
            return sourceAt(p, dims);
        }
        return midAt(p, dims);
    }
    let sig = SIGMA_PER_RADIUS * R;
    let st = max(1, (2 * n + 1 + MAX_TAPS - 1) / MAX_TAPS);
    let m = n / st;
    var acc = vec4<f32>(0.0);
    var wsum = 0.0;
    for (var j = -m; j <= m; j++) {
        let x = f32(j * st);
        let w = exp(-x * x / (2.0 * sig * sig));
        let q = p + dir * (j * st);
        var v = vec4<f32>(0.0);
        if (fromSource) {
            v = sourceAt(q, dims);
        } else {
            v = midAt(q, dims);
        }
        acc += w * v;
        wsum += w;
    }
    return acc / wsum;
}

// ---- blend modes (straight colours, b = original/backdrop, s = glow) — same formulas as _ae_fx_mix.wgsl
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

// m = _ae_fx_mix.wgsl blend code
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
        case 12: { return b + s; }                          // Add: unclipped (measured; the result is clipped at the end)
        case 13: { return max(0.0, b + s - 1.0); }
        case 19: { return clamp(b + 2.0 * s - 1.0, 0.0, 1.0); }
        case 20: {
            if (s <= 0.5) { if (s <= 0.0) { return 0.0; } return 1.0 - min(1.0, (1.0 - b) / (2.0 * s)); }
            if (s >= 1.0) { return 1.0; }
            return min(1.0, b / (2.0 * (1.0 - s)));
        }
        case 21: { if (s <= 0.5) { return min(b, 2.0 * s); } return max(b, 2.0 * s - 1.0); }
        default: { return s; }
    }
}

fn blendf(b: vec3<f32>, s: vec3<f32>, m: i32) -> vec3<f32> {
    if (m == 15) { return setLum(setSat(s, sat(b)), lum(b)); }
    if (m == 16) { return setLum(setSat(b, sat(s)), lum(b)); }
    if (m == 17) { return setLum(s, lum(b)); }
    return vec3<f32>(ch(b.r, s.r, m), ch(b.g, s.g, m), ch(b.b, s.b, m));
}

// AE Glow Operation (1..25) -> blend code above (-1 = None)
fn opCode(op: i32) -> i32 {
    var codes = array<i32, 26>(12, -1, 0, 12, 1, 2, 3, 9, 8, 19, 20, 21, 6, 6, 12, 7, 7, 13, 4, 5, 10, 10, 11, 15, 16, 17);
    return codes[clamp(op, 0, 25)];
}

// A & B colour of glow value v
fn abColor(v: f32) -> vec3<f32> {
    var x = v * max(P.cycles, 1.0) + P.phase / 360.0;
    x = x - floor(x);
    let mode = i32(round(P.looping));
    var w = 0.0;                                       // weight of Color A
    if (mode == 1) {
        w = 1.0 - x;                                   // Sawtooth A>B (unmeasured)
    } else if (mode == 2) {
        w = x;                                         // Sawtooth B>A (unmeasured)
    } else if (mode == 4) {
        w = abs(2.0 * x - 1.0);                        // Triangle B>A>B (unmeasured: mirror of the measured mode 3)
    } else {
        w = 1.0 - abs(2.0 * x - 1.0);                  // Triangle A>B>A (measured: B at v = 0 and 1, A at v = 0.5)
    }
    let mid = clamp(P.midpoint, 0.01, 0.99);
    if (abs(mid - 0.5) > 1e-4) {
        w = 1.0 - pow(1.0 - w, log(0.5) / log(mid));   // midpoint = position of the 50 % mix (unmeasured)
    }
    return mix(clamp(P.colorB.rgb, vec3<f32>(0.0), vec3<f32>(1.0)), clamp(P.colorA.rgb, vec3<f32>(0.0), vec3<f32>(1.0)), w);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(origTex));
    let p = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0), dims - vec2<i32>(1));
    let dm = i32(round(P.dims));                       // 1 = H and V, 2 = H, 3 = V
    if (P.passIndex < 0.5) {
        var g = vec4<f32>(0.0);
        if (dm != 3) {
            g = blur1(p, vec2<i32>(1, 0), dims, true);
        } else {
            g = sourceAt(p, dims);
        }
        if (abMode()) {
            let q = round(clamp(g.r, 0.0, 1.0) * 65535.0);
            let hi = floor(q / 256.0);
            return vec4<f32>(hi / 255.0, (q - hi * 256.0) / 255.0, 0.0, 1.0);
        }
        return clamp(g, vec4<f32>(0.0), vec4<f32>(1.0));
    }
    var g = vec4<f32>(0.0);
    if (dm != 2) {
        g = blur1(p, vec2<i32>(0, 1), dims, false);
    } else {
        g = midAt(p, dims);
    }
    let I = max(P.intensity, 0.0);
    var glow = vec4<f32>(0.0);                         // premultiplied glow layer
    if (abMode()) {
        let v = clamp(I * g.r, 0.0, 1.0);
        glow = vec4<f32>(abColor(v) * v, v);
    } else {
        glow = vec4<f32>(min(I * g.rgb, vec3<f32>(g.a)), g.a);
    }
    let o = textureLoad(origTex, p, 0);
    let comp = i32(round(P.composite));
    if (comp == 3) {
        return glow;                                   // Composite Original None: the glow alone (unmeasured)
    }
    // glow combined with the original by the Glow Operation (AE's separable blend rule)
    let m = opCode(i32(round(P.operation)));
    if (m < 0) {
        return o;                                      // Glow Operation None: the glow is not combined (unmeasured)
    }
    var L = o;
    {
        let ag = glow.a;
        let ao = o.a;
        var cg = vec3<f32>(0.0);
        if (ag > 0.0) { cg = glow.rgb / ag; }
        var co = vec3<f32>(0.0);
        if (ao > 0.0) { co = o.rgb / ao; }
        var bl = cg;
        if (m != 0) {
            bl = blendf(co, cg, m);
        }
        L = vec4<f32>(glow.rgb * (1.0 - ao) + o.rgb * (1.0 - ag) + ag * ao * bl, ag + ao - ag * ao);
    }
    var r = vec4<f32>(0.0);
    if (comp == 1) {
        r = o + L * (1.0 - o.a);                       // On Top: the original over the glow (unmeasured)
    } else {
        r = L + o * (1.0 - L.a);                       // Behind (measured): the glow-combined layer over the original
    }
    let a = clamp(r.a, 0.0, 1.0);
    return vec4<f32>(clamp(r.rgb, vec3<f32>(0.0), vec3<f32>(a)), a);
}
