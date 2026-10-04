// After Effects "Roughen Edges" (ADBE Roughen Edges) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Parameter positions measured. The layer's alpha is averaged over a disc of Border px (an edge distance estimate),
// pushed by fractal noise (Fractal Influence, Scale %, Stretch Width or Height, Offset, Complexity, Evolution, Random
// Seed) and re-thresholded with Edge Sharpness. Edge Type (measured menu, 8 kinds) 1 Roughen, 3 Cut, 4 Spiky, 5 Rusty,
// 7 Photocopy cut the alpha; 2 Roughen Color, 6 Rusty Color, 8 Photocopy Color also tint the border band with Edge
// Color. The pattern differs from After Effects (proprietary noise).
struct Params {
    size: vec2<f32>,
    offset: vec2<f32>,    // AE 8 Offset (Turbulence) (layer px)
    edgeColor: vec4<f32>, // AE 2 Edge Color
    edgeType: f32,        // AE 1 Edge Type (menu)
    border: f32,          // AE 3 Border (px)
    sharpness: f32,       // AE 4 Edge Sharpness
    influence: f32,       // AE 5 Fractal Influence
    scale: f32,           // AE 6 Scale (%)
    stretch: f32,         // AE 7 Stretch Width or Height
    complexity: f32,      // AE 9 Complexity
    evolution: f32,       // AE 10 Evolution (degrees)
    seed: f32,            // AE 14 Random Seed
    passIndex: f32,
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

fn rnoise(q: vec2<f32>) -> f32 {
    let st = clamp(P.stretch / 100.0, -0.99, 0.99);
    let sc = max(P.scale, 1.0) / 100.0 * 40.0 * vec2<f32>(1.0 + st, 1.0 - st);
    var u = (q - layerPt(P.offset)) / sc;
    let c = clamp(P.complexity, 1.0, 10.0);
    var sum = 0.0;
    var tot = 0.0;
    var w = 1.0;
    for (var o = 0; o < 10; o++) {
        let k = clamp(c - f32(o), 0.0, 1.0) * w;
        if (k <= 0.0) { break; }
        sum += k * vnoise(u, u32(max(P.seed, 0.0)) * 0x632be5abu + u32(o) * 0xc2b2ae35u, P.evolution / 360.0, 3);
        tot += k;
        u = u * 2.0 + vec2<f32>(13.1, 7.7);
        w *= 0.5;
    }
    return sum / max(tot, 1e-6);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let s = tapL(q);
    let b = max(P.border, 0.0);
    var ab = s.a;
    if (b > 0.5) {
        var acc = 0.0;
        for (var i = 0; i < 24; i++) {
            let t = (f32(i) + 0.5) / 24.0;
            let a = f32(i) * 2.39996323;
            acc += tapL(q + vec2<f32>(cos(a), sin(a)) * sqrt(t) * b).a;
        }
        ab = acc / 24.0;
    }
    let n = rnoise(q) * clamp(P.influence, 0.0, 1.0);
    let sh = max(P.sharpness, 0.0) * 2.0 + 1.0;
    let a = clamp((ab - 0.5 + 0.5 * n) * sh + 0.5, 0.0, 1.0) * select(1.0, 0.0, s.a <= 0.0 && b <= 0.5);
    let et = i32(round(P.edgeType));
    var c = straight8(s);
    if (et == 2 || et == 6 || et == 8) {
        let band = clamp(1.0 - abs(ab - 0.5) * 2.0 * sh, 0.0, 1.0);
        c = mix(c, P.edgeColor.rgb, band);
    }
    return out8(c, min(a, max(s.a, a * step(0.0, ab))));
}
