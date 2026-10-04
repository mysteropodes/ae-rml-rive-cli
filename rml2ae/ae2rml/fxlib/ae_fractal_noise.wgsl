// After Effects "Fractal Noise" (ADBE Fractal Noise) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Parameter positions: Fractal Noise's measured list (fxlib/_ae_params.json).
// A value-noise sum over Complexity octaves (Sub Influence %, Sub Scaling %, Sub Rotation, Sub Offset per octave),
// Noise Type 1 Block, 2 Linear, 3 Soft Linear, 4 Spline; Fractal Type 1 Basic (signed sum), 2-4 Turbulent (sum of
// |octaves|), others as Basic. Scale / Scale Width / Height % (100 % = 100 px lattice), Rotation, Offset Turbulence;
// Contrast %, Brightness, Invert, Overflow 1 Clip (others clamp too in 8 bpc). Blending Mode 1 None (the noise alone),
// 2 Normal (over the layer by Opacity), 3 Add, 4 Multiply, 5 Screen, 6 Overlay, 7 Soft Light, 8 Hard Light, 9 Color
// Dodge, 10 Color Burn, 11 Darken, 12 Lighten, 13 Difference, 14 Exclusion (assumed menu). AE's noise is
// proprietary: the pattern differs from After Effects (same scale and contrast). Cycle Evolution and Perspective
// Offset are not modelled.
struct Params {
    size: vec2<f32>,
    offset: vec2<f32>,    // AE 13 Offset Turbulence (layer px)
    subOffset: vec2<f32>, // AE 21 Sub Offset
    fractalType: f32,     // AE 1 Fractal Type (menu)
    noiseType: f32,       // AE 2 Noise Type (menu)
    invert: f32,          // AE 3 Invert
    contrast: f32,        // AE 4 Contrast (%)
    brightness: f32,      // AE 5 Brightness
    overflow: f32,        // AE 6 Overflow (menu)
    rotation: f32,        // AE 8 Rotation (degrees)
    uniform: f32,         // AE 9 Uniform Scaling
    scale: f32,           // AE 10 Scale (%)
    scaleW: f32,          // AE 11 Scale Width (%)
    scaleH: f32,          // AE 12 Scale Height (%)
    complexity: f32,      // AE 16 Complexity
    subInfluence: f32,    // AE 18 Sub Influence (%)
    subScale: f32,        // AE 19 Sub Scaling (%)
    subRotation: f32,     // AE 20 Sub Rotation (degrees)
    evolution: f32,       // AE 24 Evolution (degrees)
    seed: f32,            // AE 28 Random Seed
    opacity: f32,         // AE 30 Opacity (%)
    mode: f32,            // AE 31 Blending Mode (menu)
    passIndex: f32,
    pad0: f32,
    layerRect: vec4<f32>, // reserved, filled by the host: the layer's rect in the canvas (x0, y0, x1, y1)
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

// pixel of the canvas under this fragment, clamped to the texture
fn pixelOf(uv: vec2<f32>) -> vec2<i32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    return clamp(vec2<i32>(floor(uv * vec2<f32>(dimi))), vec2<i32>(0), dimi - vec2<i32>(1));
}

// premultiplied texel -> AE's straight 8-bit colour (black where transparent)
fn straight8(s: vec4<f32>) -> vec3<f32> {
    if (s.a <= 0.0) {
        return vec3<f32>(0.0);
    }
    return clamp(round(s.rgb / s.a * 255.0) / 255.0, vec3<f32>(0.0), vec3<f32>(1.0));
}

// straight colour + alpha -> 8-bit rounded, premultiplied output
fn out8(c: vec3<f32>, a: f32) -> vec4<f32> {
    let oc = round(clamp(c, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0) / 255.0;
    let oa = round(clamp(a, 0.0, 1.0) * 255.0) / 255.0;
    return vec4<f32>(oc * oa, oa);
}

// layer px of the centre of this fragment's canvas pixel
fn layerPos(uv: vec2<f32>) -> vec2<f32> {
    return vec2<f32>(pixelOf(uv)) + vec2<f32>(0.5) - P.layerRect.xy;
}

fn layerSize() -> vec2<f32> {
    return max(P.layerRect.zw - P.layerRect.xy, vec2<f32>(1.0));
}

// bilinear sample of the layer at layer px q (pixel i centre = i + 0.5), premultiplied, transparent outside the layer
fn tapL(q: vec2<f32>) -> vec4<f32> {
    let size = layerSize();
    let p = q - vec2<f32>(0.5);
    let o = max(-p, p - (size - vec2<f32>(1.0)));
    let cov = clamp(vec2<f32>(1.0) - o, vec2<f32>(0.0), vec2<f32>(1.0));
    let c = cov.x * cov.y;
    let dims = vec2<f32>(textureDimensions(srcTex, 0));
    if (c <= 0.0) {
        return vec4<f32>(0.0);
    }
    return c * textureSampleLevel(srcTex, srcSamp, (q + P.layerRect.xy) / dims, 0.0);
}

// a point parameter (the host writes it in canvas px, i.e. layer px + fxPad) -> layer px
fn layerPt(c: vec2<f32>) -> vec2<f32> {
    return c - P.layerRect.xy;
}

// AE angle convention: 0 = up, clockwise (y down)
fn aeDir(deg: f32) -> vec2<f32> {
    let a = radians(deg);
    return vec2<f32>(sin(a), -cos(a));
}

fn pcg(v: u32) -> u32 {
    let s = v * 747796405u + 2891336453u;
    let w = ((s >> ((s >> 28u) + 4u)) ^ s) * 277803737u;
    return (w >> 22u) ^ w;
}
fn unit(h: u32) -> f32 { return f32(h >> 8u) * (1.0 / 16777216.0); }

// lattice value in -1..1, turning with the evolution (revolutions) like After Effects' noises (pattern differs)
fn lat(ix: i32, iy: i32, salt: u32, evo: f32) -> f32 {
    let h = pcg(bitcast<u32>(ix) ^ pcg(bitcast<u32>(iy) ^ pcg(salt)));
    let a = unit(h) * 2.0 - 1.0;
    let b = unit(pcg(h ^ 0x9e3779b9u)) * 2.0 - 1.0;
    let t = 6.28318531 * evo * (0.5 + unit(pcg(h + 0x7f4a7c15u)));
    return a * cos(t) + b * sin(t);
}

// value noise with interpolation kind 1 Block, 2 Linear, 3 Soft Linear, 4 Spline (cubic B-spline)
fn vnoise(q: vec2<f32>, salt: u32, evo: f32, kind: i32) -> f32 {
    let i = floor(q);
    let f = q - i;
    let ix = i32(i.x);
    let iy = i32(i.y);
    if (kind == 1) {
        return lat(ix, iy, salt, evo);
    }
    if (kind == 4) {
        let s = vec2<f32>(1.0) - f;
        let wx = vec4<f32>(s.x * s.x * s.x, 3.0 * f.x * f.x * f.x - 6.0 * f.x * f.x + 4.0,
                           -3.0 * f.x * f.x * f.x + 3.0 * f.x * f.x + 3.0 * f.x + 1.0, f.x * f.x * f.x) / 6.0;
        let wy = vec4<f32>(s.y * s.y * s.y, 3.0 * f.y * f.y * f.y - 6.0 * f.y * f.y + 4.0,
                           -3.0 * f.y * f.y * f.y + 3.0 * f.y * f.y + 3.0 * f.y + 1.0, f.y * f.y * f.y) / 6.0;
        var acc = 0.0;
        for (var b = 0; b < 4; b++) {
            var row = 0.0;
            for (var a = 0; a < 4; a++) {
                row += wx[a] * lat(ix + a - 1, iy + b - 1, salt, evo);
            }
            acc += wy[b] * row;
        }
        return acc * 1.5;
    }
    var u = f;
    if (kind == 3) {
        u = f * f * (3.0 - 2.0 * f);
    }
    let v00 = lat(ix, iy, salt, evo);
    let v10 = lat(ix + 1, iy, salt, evo);
    let v01 = lat(ix, iy + 1, salt, evo);
    let v11 = lat(ix + 1, iy + 1, salt, evo);
    return mix(mix(v00, v10, u.x), mix(v01, v11, u.x), u.y);
}

fn rot2(v: vec2<f32>, deg: f32) -> vec2<f32> {
    let a = radians(deg);
    return vec2<f32>(v.x * cos(a) - v.y * sin(a), v.x * sin(a) + v.y * cos(a));
}

// separable blend modes on straight colour (b = base, s = the other layer); menu order assumed (to be measured):
// 1 Normal 2 Add 3 Darken 4 Multiply 5 Color Burn 6 Linear Burn 7 Lighten 8 Screen 9 Color Dodge 10 Linear Dodge
// 11 Overlay 12 Soft Light 13 Hard Light 14 Linear Light 15 Vivid Light 16 Pin Light 17 Hard Mix 18 Difference
// 19 Exclusion
fn burn(b: f32, s: f32) -> f32 {
    if (b >= 1.0) { return 1.0; }
    if (s <= 0.0) { return 0.0; }
    return 1.0 - min(1.0, (1.0 - b) / s);
}
fn dodge(b: f32, s: f32) -> f32 {
    if (b <= 0.0) { return 0.0; }
    if (s >= 1.0) { return 1.0; }
    return min(1.0, b / (1.0 - s));
}
fn hard(b: f32, s: f32) -> f32 {
    if (s <= 0.5) { return b * 2.0 * s; }
    return 1.0 - (1.0 - b) * (1.0 - (2.0 * s - 1.0));
}
fn soft(b: f32, s: f32) -> f32 {
    if (s <= 0.5) { return b - (1.0 - 2.0 * s) * b * (1.0 - b); }
    var d = sqrt(b);
    if (b <= 0.25) { d = ((16.0 * b - 12.0) * b + 4.0) * b; }
    return b + (2.0 * s - 1.0) * (d - b);
}
fn blend1(b: f32, s: f32, mode: i32) -> f32 {
    switch mode {
        case 2: { return b + s; }
        case 3: { return min(b, s); }
        case 4: { return b * s; }
        case 5: { return burn(b, s); }
        case 6: { return b + s - 1.0; }
        case 7: { return max(b, s); }
        case 8: { return 1.0 - (1.0 - b) * (1.0 - s); }
        case 9: { return dodge(b, s); }
        case 10: { return b + s; }
        case 11: { return hard(s, b); }
        case 12: { return soft(b, s); }
        case 13: { return hard(b, s); }
        case 14: { return b + 2.0 * s - 1.0; }
        case 15: { if (s <= 0.5) { return burn(b, 2.0 * s); } return dodge(b, 2.0 * s - 1.0); }
        case 16: { if (s <= 0.5) { return min(b, 2.0 * s); } return max(b, 2.0 * s - 1.0); }
        case 17: { return select(0.0, 1.0, b + s >= 1.0); }
        case 18: { return abs(b - s); }
        case 19: { return b + s - 2.0 * b * s; }
        default: { return s; }
    }
}
fn blendRGB(b: vec3<f32>, s: vec3<f32>, mode: i32) -> vec3<f32> {
    return clamp(vec3<f32>(blend1(b.r, s.r, mode), blend1(b.g, s.g, mode), blend1(b.b, s.b, mode)), vec3<f32>(0.0), vec3<f32>(1.0));
}

fn field(q: vec2<f32>) -> f32 {
    var sc = vec2<f32>(P.scale / 100.0 * 100.0);
    if (P.uniform < 0.5) {
        sc = vec2<f32>(P.scaleW, P.scaleH);
    }
    sc = max(sc, vec2<f32>(0.01));
    var u = rot2(q - layerPt(P.offset), -P.rotation) / sc;
    let kind = i32(round(P.noiseType));
    let ft = i32(round(P.fractalType));
    let turb = ft >= 2 && ft <= 4;
    let c = clamp(P.complexity, 1.0, 20.0);
    let evo = P.evolution / 360.0;
    let seed = u32(max(P.seed, 0.0));
    var sum = 0.0;
    var total = 0.0;
    var infl = 1.0;
    for (var o = 0; o < 20; o++) {
        let w = clamp(c - f32(o), 0.0, 1.0) * infl;
        if (w <= 0.0) { break; }
        var n = vnoise(u + vec2<f32>(f32(o) * 17.31, f32(o) * 41.17), seed * 0x632be5abu + u32(o) * 0xc2b2ae35u, evo, kind);
        if (turb) { n = abs(n) * 2.0 - 1.0; }
        sum += w * n;
        total += w;
        infl *= clamp(P.subInfluence / 100.0, 0.0, 1.0);
        u = rot2(u, -P.subRotation) * (100.0 / max(P.subScale, 1.0)) + layerPt(P.subOffset) / sc;
    }
    var v = 0.5 + 0.5 * sum / max(total, 1e-6) * 1.6;
    if (ft == 4) { v = pow(clamp(v, 0.0, 1.0), 2.0); }
    v = (v - 0.5) * P.contrast / 100.0 + 0.5 + P.brightness / 100.0;
    if (P.invert > 0.5) { v = 1.0 - v; }
    return clamp(v, 0.0, 1.0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let q = layerPos(in.uv);
    let sz = layerSize();
    if (q.x < 0.0 || q.y < 0.0 || q.x >= sz.x || q.y >= sz.y) {
        return s;
    }
    let v = vec3<f32>(field(q));
    let op = clamp(P.opacity / 100.0, 0.0, 1.0);
    let mode = i32(round(P.mode));
    let c = straight8(s);
    if (mode <= 1) {
        return out8(v, op);
    }
    if (mode == 2) {
        let a = op + s.a * (1.0 - op);
        return out8((v * op + c * s.a * (1.0 - op)) / max(a, 1e-4), a);
    }
    var m = 1;
    switch mode {
        case 3: { m = 2; }
        case 4: { m = 4; }
        case 5: { m = 8; }
        case 6: { m = 11; }
        case 7: { m = 12; }
        case 8: { m = 13; }
        case 9: { m = 9; }
        case 10: { m = 5; }
        case 11: { m = 3; }
        case 12: { m = 7; }
        case 13: { m = 18; }
        case 14: { m = 19; }
        default: {}
    }
    return out8(mix(c, blendRGB(c, v, m), op), s.a);
}
