// After Effects "4-Color Gradient" (ADBE 4ColorGradient) — measured against AE 2026 (8 bpc), pixel-exact in mode None.
//
// Algorithm (fitted on the AE refs, max error 0 levels on opaque pixels):
//   * evaluated at the INTEGER pixel position (x, y) of the layer (no +0.5 centre offset);
//   * weight_i = 1 / (|p - Point_i|^2 + Blend^2)  (inverse-square distance softened by Blend, in px);
//   * gradient = sum(w_i * Colour_i) / sum(w_i), straight colour, quantised with FLOOR to 8 bits (G8);
//   * Blending Mode "None" (1, default): the layer becomes the gradient, the layer alpha is kept and scaled by Opacity:
//       A = floor(srcA8 * Opacity / 100), premultiplied RGB = (G8 * A + 127) div 255 (AE's 8-bit premultiply).
//   * Jitter: AE's noise is proprietary; reproduced as a per-pixel dither of +-Jitter/200 of an 8-bit level added
//     before the floor (Jitter 100 = +-0.5 level), which only breaks banding — not measured against AE.
//   * Blending modes 2..22: the gradient is blended over the (straight) original with the layer's mode formula,
//     mixed by Opacity, layer alpha kept — not measured against AE (menu order 19..22 assumed:
//     Stencil Alpha, Silhouette Alpha, Alpha Add, Luminescent Premul).
struct Params {
    size: vec2<f32>,
    point1: vec2<f32>,    // AE 2 Point 1 (layer px)
    point2: vec2<f32>,    // AE 4 Point 2 (layer px)
    point3: vec2<f32>,    // AE 6 Point 3 (layer px)
    point4: vec2<f32>,    // AE 8 Point 4 (layer px)
    pad0: vec2<f32>,
    color1: vec4<f32>,    // AE 3 Color 1 (straight color 0..1)
    color2: vec4<f32>,    // AE 5 Color 2 (straight color 0..1)
    color3: vec4<f32>,    // AE 7 Color 3 (straight color 0..1)
    color4: vec4<f32>,    // AE 9 Color 4 (straight color 0..1)
    blend: f32,           // AE 11 Blend 5..10000 (px)
    jitter: f32,          // AE 12 Jitter 0..500
    opacity: f32,         // AE 13 Opacity 0..100 %
    blendMode: f32,       // AE 14 Blending Mode 1..22 (1 None, 2 Normal, 3 Add, 4 Multiply, 5 Screen, 6 Overlay, 7 Soft Light, 8 Hard Light, 9 Color Dodge, 10 Color Burn, 11 Darken, 12 Lighten, 13 Difference, 14 Exclusion, 15 Hue, 16 Saturation, 17 Color, 18 Luminosity, 19 Stencil Alpha, 20 Silhouette Alpha, 21 Alpha Add, 22 Luminescent Premul)
    passIndex: f32,
    pad1: f32,
    pad2: f32,
    pad3: f32,
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

fn hash2(p: vec2<f32>) -> f32 {
    // integer hash (pcg-like) of the pixel index -> [0, 1)
    var h: u32 = (u32(p.x) * 1597334677u) ^ (u32(p.y) * 3812015801u);
    h = h * 747796405u + 2891336453u;
    h = ((h >> ((h >> 28u) + 4u)) ^ h) * 277803737u;
    h = (h >> 22u) ^ h;
    return f32(h) / 4294967296.0;
}

fn weight(p: vec2<f32>, q: vec2<f32>, b2: f32) -> f32 {
    let d = p - q;
    return 1.0 / (dot(d, d) + b2);
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
    let s = textureSample(srcTex, srcSamp, in.uv);          // premultiplied layer content
    let px = floor(in.uv * P.size);                          // AE evaluates at the integer pixel position
    let b2 = P.blend * P.blend;
    let w1 = weight(px, P.point1, b2);
    let w2 = weight(px, P.point2, b2);
    let w3 = weight(px, P.point3, b2);
    let w4 = weight(px, P.point4, b2);
    let c1 = floor(clamp(P.color1.rgb, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5);
    let c2 = floor(clamp(P.color2.rgb, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5);
    let c3 = floor(clamp(P.color3.rgb, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5);
    let c4 = floor(clamp(P.color4.rgb, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5);
    // weighted mean written relative to colour 1 so that equal channels stay exact integers in f32
    let g = c1 + (w2 * (c2 - c1) + w3 * (c3 - c1) + w4 * (c4 - c1)) / (w1 + w2 + w3 + w4);   // 0..255
    let dither = (hash2(px) - 0.5) * max(P.jitter, 0.0) / 100.0;
    let g8 = clamp(floor(g + dither + 1e-5), vec3<f32>(0.0), vec3<f32>(255.0));
    let op = clamp(P.opacity, 0.0, 100.0) / 100.0;
    let a8 = floor(s.a * 255.0 + 0.5);
    let mode = i32(round(P.blendMode));
    if (mode <= 1) {
        // None: gradient only, layer alpha * opacity, AE 8-bit premultiply (x * a + 127) / 255
        let oa = floor(a8 * op + 1e-3);
        let rgb = floor((g8 * oa + 127.0) / 255.0);
        return vec4<f32>(rgb / 255.0, oa / 255.0);
    }
    if (a8 <= 0.0) {
        return vec4<f32>(0.0);
    }
    let sa = s.a;
    let orig = clamp(s.rgb / sa, vec3<f32>(0.0), vec3<f32>(1.0));
    let gr = g8 / 255.0;
    if (mode == 19) {                                        // Stencil Alpha: gradient alpha (= 1) masks the layer
        return s;
    }
    if (mode == 20) {                                        // Silhouette Alpha: layer cut by the gradient alpha
        return s * (1.0 - op);
    }
    if (mode == 21) {                                        // Alpha Add: normal colour, alphas added (layer alpha kept)
        return vec4<f32>(mix(orig, gr, op) * sa, sa);
    }
    if (mode == 22) {                                        // Luminescent Premul: premultiplied gradient added
        return vec4<f32>(min(s.rgb + gr * op * sa, vec3<f32>(sa)), sa);
    }
    let bl = clamp(blendRGB(mode, orig, gr), vec3<f32>(0.0), vec3<f32>(1.0));
    return vec4<f32>(mix(orig, bl, op) * sa, sa);
}
