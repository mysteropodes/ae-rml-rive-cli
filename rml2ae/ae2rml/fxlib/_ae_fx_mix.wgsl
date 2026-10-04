// fxlib helper: a layer's effect group composited back over what it was applied to — the adjustment layer's opacity /
// in-out window (amount) and its blend mode. Normal: exact lerp(original, effect result, amount) (drawing the two
// images with opacities is not a lerp once the effect is opaque). Other modes: see fs_main (AE's rule, measured).
struct Params {
    size: vec2<f32>,
    amount: f32,      // 0..1
    blend: f32,       // 0 normal, 1 multiply, 2 screen, 3 overlay, 4 darken, 5 lighten, 6 color dodge, 7 color burn,
                      // 8 hard light, 9 soft light, 10 difference, 11 exclusion, 12 add, 13 linear burn, 14 subtract,
                      // 15 hue, 16 saturation, 17 color, 18 luminosity, 19 linear light, 20 vivid light, 21 pin light,
                      // 22 hard mix, 23 lighter color, 24 darker color, 25 divide
    passIndex: f32,
    pad0: f32,
    pad1: f32,
    pad2: f32,
};
@group(0) @binding(0) var srcTex: texture_2d<f32>;     // the effect group's output
@group(0) @binding(1) var srcSamp: sampler;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var origTex: texture_2d<f32>;    // what the group was applied to

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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let fx = textureSample(srcTex, srcSamp, in.uv);
    let o = textureSample(origTex, srcSamp, in.uv);
    let a = clamp(P.amount, 0.0, 1.0);
    let m = i32(P.blend + 0.5);
    if (m == 0) {
        return mix(o, fx, a);
    }
    // AE (measured on a half-transparent backdrop, Overlay 50 %): the effect result is composited onto the original
    // with the blend mode (W3C, at full opacity), re-matted by the ORIGINAL's alpha (an adjustment layer does not
    // change coverage), then interpolated by the opacity
    let ab = o.a;
    let as_ = fx.a;
    var cb = vec3<f32>(0.0);
    if (ab > 0.0) { cb = o.rgb / ab; }
    var cs = vec3<f32>(0.0);
    if (as_ > 0.0) { cs = fx.rgb / as_; }
    let bl = blendf(cb, cs, m);
    let co = cs * as_ * (1.0 - ab) + o.rgb * (1.0 - as_) + as_ * ab * bl;
    let ao = as_ + ab * (1.0 - as_);
    var xs = vec3<f32>(0.0);
    if (ao > 0.0) { xs = co / ao; }
    return mix(o, vec4<f32>(xs * ab, ab), a);
}
