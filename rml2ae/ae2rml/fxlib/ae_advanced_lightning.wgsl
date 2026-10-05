// After Effects "Advanced Lightning" (ADBE Lightning 2) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// One bolt from Origin to Direction (Lightning Type 1 Direction; the other types are drawn the same way here), its path
// displaced sideways by a fractal noise of Turbulence (Conductivity State seeds it). Core of Core Radius px, Core
// Opacity %, Core Color; Glow of Glow Radius px, Glow Opacity %, Glow Color, added over the layer. Forks, Decay, Alpha
// Obstacle and the other types are not modelled. Indices checked against AE 26. AE's bolts are random: they differ.
struct Params {
    size: vec2<f32>,
    origin: vec2<f32>,    // AE 2 Origin (layer px)
    toPt: vec2<f32>,      // AE 3 Direction (layer px)
    coreColor: vec4<f32>, // AE 8 Core Color
    glowColor: vec4<f32>, // AE 13 Glow Color
    kind: f32,            // AE 1 Lightning Type (menu)
    conductivity: f32,    // AE 4 Conductivity State
    coreRadius: f32,      // AE 6 Core Radius (px)
    coreOpacity: f32,     // AE 7 Core Opacity (%)
    glowRadius: f32,      // AE 11 Glow Radius (px)
    glowOpacity: f32,     // AE 12 Glow Opacity (%)
    turbulence: f32,      // AE 16 Turbulence
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

fn bolt(t: f32, L: f32) -> f32 {           // sideways offset (px) at fraction t
    var o = 0.0;
    var amp = 0.12 * L * clamp(P.turbulence, 0.0, 5.0) / 2.0;
    var fr = 3.0;
    for (var k = 0; k < 6; k++) {
        o += amp * vnoise(vec2<f32>(t * fr, f32(k) * 7.0), u32(k) * 977u, P.conductivity / 10.0, 2);
        amp *= 0.5;
        fr *= 2.2;
    }
    return o * sin(3.14159265 * t);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let q = layerPos(in.uv);
    let a = layerPt(P.origin);
    let b = layerPt(P.toPt);
    let ab = b - a;
    let L = max(length(ab), 1.0);
    let u = ab / L;
    let n = vec2<f32>(-u.y, u.x);
    var dmin = 1e9;
    var prev = a;
    for (var i = 1; i <= 64; i++) {
        let t = f32(i) / 64.0;
        let cur = a + ab * t + n * bolt(t, L);
        let e = cur - prev;
        let h = clamp(dot(q - prev, e) / max(dot(e, e), 1e-6), 0.0, 1.0);
        dmin = min(dmin, length(q - prev - e * h));
        prev = cur;
    }
    let core = (1.0 - smoothstep(max(P.coreRadius, 0.1) * 0.5, max(P.coreRadius, 0.1) * 1.5, dmin)) * P.coreOpacity / 100.0;
    let glow = exp(-dmin * dmin / max(P.glowRadius * P.glowRadius * 0.25, 0.25)) * P.glowOpacity / 100.0;
    let add = P.coreColor.rgb * core + P.glowColor.rgb * glow * (1.0 - core);
    let ad = clamp(max(core, glow), 0.0, 1.0);
    let c = s.rgb + add;
    return round(clamp(vec4<f32>(c, max(s.a, ad)), vec4<f32>(0.0), vec4<f32>(1.0)) * 255.0) / 255.0;
}
