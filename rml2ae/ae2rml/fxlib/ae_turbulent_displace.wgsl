// After Effects "Turbulent Displace" (ADBE Turbulent Displace) — the layer is resampled at p + D(p) (bilinear,
// transparent outside the layer), D = a 2-channel fractal noise field. Measured against AE 2026 (two refs, field
// recovered by photometric fit to 0.07/255): D(p) = Amount/100 * Size * N((p - Offset) / Size), the noise is centred
// on Offset (Turbulence), its two channels (dx, dy) are independent, per-channel std ~0.27, correlation length ~0.9
// Size (Gaussian-like). "Pin All" (default) fades only the component PERPENDICULAR to each layer edge with
// smoothstep(0, 0.84 * Size, distance to that edge) (fit residual 0.18 px). AE's own noise generator (lattice hash)
// is proprietary: N here is a statistically matched cubic-B-spline value noise (lattice 1.12 Size, values U[-1,1]),
// so the warp has AE's scale, strength, edge pinning and parameter behaviour but not AE's exact pattern.
// Evolution: every lattice value rotates (a cos t + b sin t, random rate 0..1 turn per revolution); Cycle Evolution
// rounds the rates to whole turns per cycle so the field loops exactly. Complexity = octaves (AE fractal defaults:
// sub scaling 56 %, sub influence 70 %, fractional last octave), normalised by the summed influence.
struct Params {
    size: vec2<f32>,
    offset: vec2<f32>,        // AE 4 Offset (Turbulence) point, layer px (default = layer centre)
    displacement: f32,        // AE 1 Displacement popup: 1 Turbulent, 2 Bulge, 3 Twist, 5/6/7 the "Smoother" versions, 9 Vertical, 10 Horizontal, 11 Cross
    amount: f32,              // AE 2 Amount (-10000..10000, default 50)
    noiseSize: f32,           // AE 3 Size px (2..1000, default 100)
    complexity: f32,          // AE 5 Complexity (1..10, default 1)
    evolution: f32,           // AE 6 Evolution degrees
    cycleEvolution: f32,      // AE 8 Cycle Evolution checkbox 0/1
    cycleRevolutions: f32,    // AE 9 Cycle (in Revolutions) 1..88
    randomSeed: f32,          // AE 10 Random Seed 0..100000
    pinning: f32,             // AE 12 Pinning popup 1..17 (1 None, 3 Pin All = default; 2 and 10 are separators)
    resizeLayer: f32,         // AE 13 Resize Layer checkbox 0/1 (not reproduced: canvas = layer)
    antialiasing: f32,        // AE 14 Antialiasing for Best Quality popup 1 Low, 2 High
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

const LATTICE: f32 = 1.12;        // lattice spacing in Size units (matches AE's correlation length)
const SUB_SCALE: f32 = 0.56;      // AE fractal defaults
const SUB_INFL: f32 = 0.7;
const PIN_RAMP: f32 = 0.84;       // pin fade length in Size units (measured)
const GRAD_K: f32 = 0.848;        // Bulge/Twist gradient gain (same rms as the Turbulent field)
const TAU: f32 = 6.283185307;

fn pcg(v: u32) -> u32 {
    let s = v * 747796405u + 2891336453u;
    let w = ((s >> ((s >> 28u) + 4u)) ^ s) * 277803737u;
    return (w >> 22u) ^ w;
}
fn unit(h: u32) -> f32 { return f32(h >> 8u) * (1.0 / 16777216.0); }

// cubic B-spline weights for fraction t (nodes -1, 0, 1, 2) and their derivatives
fn bw(t: f32) -> vec4<f32> {
    let s = 1.0 - t;
    return vec4<f32>(s * s * s, 3.0 * t * t * t - 6.0 * t * t + 4.0, -3.0 * t * t * t + 3.0 * t * t + 3.0 * t + 1.0, t * t * t) / 6.0;
}
fn bd(t: f32) -> vec4<f32> {
    let s = 1.0 - t;
    return vec4<f32>(-0.5 * s * s, 1.5 * t * t - 2.0 * t, -1.5 * t * t + t + 0.5, 0.5 * t * t);
}

// value of lattice node (ix, iy) for noise `salt`, rotated by the evolution
fn node(ix: i32, iy: i32, salt: u32, evo: f32, cyc: f32) -> f32 {
    let h = pcg(bitcast<u32>(ix) ^ pcg(bitcast<u32>(iy) ^ pcg(salt)));
    let a = unit(h) * 2.0 - 1.0;
    let h2 = pcg(h ^ 0x9e3779b9u);
    let b = unit(h2) * 2.0 - 1.0;
    var rate = unit(pcg(h2 + 0x7f4a7c15u));               // turns per revolution
    if (cyc > 0.0) {
        rate = round(rate * cyc) / cyc;                    // whole turns per cycle -> exact loop
    }
    let t = TAU * rate * evo;
    return a * cos(t) + b * sin(t);
}

// one octave: value + gradient (lattice units) of channel `salt` at lattice coords q
fn octave(q: vec2<f32>, salt: u32, evo: f32, cyc: f32) -> vec3<f32> {
    let i = floor(q);
    let f = q - i;
    let wx = bw(f.x);
    let wy = bw(f.y);
    let dx = bd(f.x);
    let dy = bd(f.y);
    let ix = i32(i.x);
    let iy = i32(i.y);
    var acc = vec3<f32>(0.0);
    for (var b = 0; b < 4; b = b + 1) {
        var row = vec2<f32>(0.0);                          // (sum wx*v, sum dx*v)
        for (var a = 0; a < 4; a = a + 1) {
            let v = node(ix + a - 1, iy + b - 1, salt, evo, cyc);
            row = row + vec2<f32>(wx[a], dx[a]) * v;
        }
        acc = acc + vec3<f32>(row.x * wy[b], row.y * wy[b], row.x * dy[b]);
    }
    return acc;                                            // (N, dN/dqx, dN/dqy)
}

// fractal sum over Complexity octaves: returns (N, dN/dqx, dN/dqy) in base-lattice units
fn fractal(u: vec2<f32>, ch: u32, evo: f32, cyc: f32, seed: u32, cx: f32) -> vec3<f32> {
    let c = clamp(cx, 1.0, 10.0);
    var sum = vec3<f32>(0.0);
    var total = 0.0;
    var scale = 1.0;
    var infl = 1.0;
    for (var o = 0; o < 10; o = o + 1) {
        let w = clamp(c - f32(o), 0.0, 1.0) * infl;
        if (w <= 0.0) {
            break;
        }
        let salt = seed * 0x632be5abu + ch * 0x85ebca6bu + u32(o) * 0xc2b2ae35u;
        let n = octave(u / (LATTICE * scale) + vec2<f32>(f32(o) * 17.31, f32(o) * 41.17), salt, evo, cyc);
        sum = sum + w * vec3<f32>(n.x, n.yz / scale);
        total = total + w;
        scale = scale * SUB_SCALE;
        infl = infl * SUB_INFL;
    }
    return sum / max(total, 1e-6);
}

fn ramp(d: f32, r: f32) -> f32 {
    return smoothstep(0.0, r, d);
}

// premultiplied sample with transparent outside the layer (bilinear against a zero border)
fn fetch(px: vec2<f32>) -> vec4<f32> {
    let s = textureSampleLevel(srcTex, srcSamp, (px + 0.5) / P.size, 0.0);
    let cov = clamp(px + 1.0, vec2<f32>(0.0), vec2<f32>(1.0)) * clamp(P.size - px, vec2<f32>(0.0), vec2<f32>(1.0));
    return s * cov.x * cov.y;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let p = in.pos.xy - 0.5;                               // pixel coords (centres on integers)
    let sz = max(P.noiseSize, 1.0);
    let u = (p + 0.5 - P.offset) / sz;
    let evo = P.evolution / 360.0;                         // revolutions
    var cyc = 0.0;
    if (P.cycleEvolution > 0.5) {
        cyc = max(round(P.cycleRevolutions), 1.0);
    }
    let seed = u32(max(round(P.randomSeed), 0.0));
    let mode = i32(round(P.displacement));
    let k = P.amount / 100.0 * sz;

    var d = vec2<f32>(0.0);
    if (mode == 2 || mode == 3 || mode == 6 || mode == 7) {
        let g = fractal(u, 0u, evo, cyc, seed, P.complexity);
        if (mode == 2 || mode == 6) {
            d = GRAD_K * g.yz;                             // Bulge: towards the noise peaks (magnifies them)
        } else {
            d = GRAD_K * vec2<f32>(-g.z, g.y);             // Twist: around the noise peaks
        }
    } else {
        let nx = fractal(u, 0u, evo, cyc, seed, P.complexity).x;
        if (mode == 9) {
            d = vec2<f32>(0.0, nx);                        // Vertical Displacement
        } else if (mode == 10) {
            d = vec2<f32>(nx, 0.0);                        // Horizontal Displacement
        } else if (mode == 11) {
            d = vec2<f32>(nx, nx);                         // Cross Displacement
        } else {
            d = vec2<f32>(nx, fractal(u, 1u, evo, cyc, seed, P.complexity).x);   // Turbulent (and Smoother)
        }
    }
    d = d * k;

    // Pinning (plug-in strings): 1 None | 3 All 4 Horizontal 5 Vertical 6 Left 7 Right 8 Top 9 Bottom | 11..17 the same "Locked"
    let pm = i32(round(P.pinning));
    var m = pm;
    var locked = false;
    if (pm >= 11) {
        m = pm - 8;
        locked = true;
    }
    var edges = vec4<f32>(0.0);                             // left, top, right, bottom
    if (m == 3) { edges = vec4<f32>(1.0, 1.0, 1.0, 1.0); }
    else if (m == 4) { edges = vec4<f32>(0.0, 1.0, 0.0, 1.0); }
    else if (m == 5) { edges = vec4<f32>(1.0, 0.0, 1.0, 0.0); }
    else if (m == 6) { edges = vec4<f32>(1.0, 0.0, 0.0, 0.0); }
    else if (m == 7) { edges = vec4<f32>(0.0, 0.0, 1.0, 0.0); }      // Pin Right
    else if (m == 8) { edges = vec4<f32>(0.0, 1.0, 0.0, 0.0); }      // Pin Top
    else if (m == 9) { edges = vec4<f32>(0.0, 0.0, 0.0, 1.0); }
    let r = PIN_RAMP * sz;
    let fl = mix(1.0, ramp(p.x, r), edges.x);
    let ft = mix(1.0, ramp(p.y, r), edges.y);
    let fr = mix(1.0, ramp(P.size.x - 1.0 - p.x, r), edges.z);
    let fb = mix(1.0, ramp(P.size.y - 1.0 - p.y, r), edges.w);
    if (locked) {
        d = d * (fl * fr * ft * fb);
    } else {
        d = d * vec2<f32>(fl * fr, ft * fb);
    }

    let q = p + d;
    if (P.antialiasing > 1.5) {
        return 0.25 * (fetch(q + vec2<f32>(-0.25, -0.25)) + fetch(q + vec2<f32>(0.25, -0.25))
                     + fetch(q + vec2<f32>(-0.25, 0.25)) + fetch(q + vec2<f32>(0.25, 0.25)));
    }
    return fetch(q);
}
