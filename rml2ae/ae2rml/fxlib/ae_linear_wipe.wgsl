// After Effects "Linear Wipe" (ADBE Linear Wipe, FR "Volet linéaire") — measured on AE 2026 8 bpc.
//   * wipe direction d = (sin A, -cos A) (Angle 90 = wipes from the left), t = d . p with p in layer px (pixel i
//     covers [i, i + 1]); the edge sits at e = tmin + Completion/100 * (tmax - tmin), tmin / tmax = t of the layer
//     corners; pixels with t < e are wiped (40 % at 90 deg on a 640 px layer: x < 256 gone, bit-exact);
//   * the hard edge is anti-aliased by exact pixel-area coverage, then Feather = AE's Fast Blur of that matte
//     (three iterated fractional boxes of radius 0.369 x Feather per axis, the "Fast Blur (Legacy)" kernel; fitted
//     radius 14.8 at Feather 40, max 1/255 over the 30 deg ref); the matte is unbounded (not cropped to the layer);
//   * output = the layer with alpha8 = floor(alpha8 * matte), straight colour kept.
//   Not covered by a visible ref: the feather does NOT extend the travel range here (at 0 % / 100 % half the feather
//   ramp remains at the far corner) — AE may extend it.
// The matte only depends on z = t(pixel centre) - e, so it is computed as 1D tables: pass 0 = horizontal blur h(z),
// pass 1 = vertical blur g(z) of h, both tabulated over z in [-Z, Z] in the first texels of the canvas (16-bit packed
// in R/G), pass 2 = per-pixel lookup + composite with origTex. tmin / tmax use P.layerRect (the layer's rect in the
// canvas, filled by the host: correct with fxPad > 0).
struct Params {
    size: vec2<f32>,
    completion: f32,      // AE 1 Transition Completion 0..100 %
    angle: f32,           // AE 2 Wipe Angle (deg)
    feather: f32,         // AE 3 Feather 0..32000 px
    passIndex: f32,
    pad0: f32,
    pad1: f32,
    layerRect: vec4<f32>, // reserved, filled by the host: the layer's rect in the canvas (x0, y0, x1, y1)
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

const MAXT: i32 = 255;    // kernel taps per table entry; wider kernels are sampled every `stride` px
const NLUT: i32 = 16384;  // table entries (first texels of the canvas, row-major)

fn dirv() -> vec2<f32> {
    let a = radians(P.angle);
    return vec2<f32>(sin(a), -cos(a));
}

fn rho() -> f32 {
    return 0.369 * clamp(P.feather, 0.0, 32000.0);
}

// fractional box (weight 1 on |x| <= floor(rho), frac(rho) on floor(rho) + 1): first difference = 4 impulses
fn impPos(r: f32, k: i32) -> f32 {
    let n = floor(r);
    switch k {
        case 0: { return 0.0; }
        case 1: { return 1.0; }
        case 2: { return 2.0 * n + 2.0; }
        default: { return 2.0 * n + 3.0; }
    }
}

fn impW(r: f32, k: i32) -> f32 {
    let f = r - floor(r);
    switch k {
        case 0: { return f; }
        case 1: { return 1.0 - f; }
        case 2: { return f - 1.0; }
        default: { return -f; }
    }
}

// three identical boxes: K(u) = triple running sum of the 64 impulses (unnormalised); s0 = first tap (<= 0)
fn kernelAt(u: f32, r: f32, s0: f32) -> f32 {
    var acc = 0.0;
    for (var a = 0; a < 4; a = a + 1) {
        for (var b = 0; b < 4; b = b + 1) {
            let pab = impPos(r, a) + impPos(r, b);
            let wab = impW(r, a) * impW(r, b);
            for (var c = 0; c < 4; c = c + 1) {
                let m = u - s0 - pab - impPos(r, c);
                if (m >= 0.0) {
                    acc = acc + wab * impW(r, c) * (m + 1.0) * (m + 2.0) * 0.5;
                }
            }
        }
    }
    return acc;
}

// coverage of a unit pixel by the half plane t > e, z = t(centre) - e: CDF of the sum of two uniforms of widths
// |dx| and |dy| (a trapezoid)
fn coverage(z: f32, d: vec2<f32>) -> f32 {
    let a = max(abs(d.x), abs(d.y));
    let b = min(abs(d.x), abs(d.y));
    let hi = 0.5 * (a + b);
    let lo = 0.5 * (a - b);
    if (z <= -hi) {
        return 0.0;
    }
    if (z >= hi) {
        return 1.0;
    }
    if (b < 1e-5) {
        return clamp(0.5 + z / a, 0.0, 1.0);
    }
    if (z < -lo) {
        let u = z + hi;
        return u * u / (2.0 * a * b);
    }
    if (z > lo) {
        let u = hi - z;
        return 1.0 - u * u / (2.0 * a * b);
    }
    return 0.5 + z / a;
}

fn halfSpan(d: vec2<f32>) -> f32 {
    let s0 = 3.0 * floor(rho()) + 3.0;
    return 0.5 * (abs(d.x) + abs(d.y)) + s0 * (abs(d.x) + abs(d.y)) + 1.0;
}

fn nLut(dim: vec2<i32>) -> i32 {
    return min(dim.x * dim.y, NLUT);
}

fn pack16(v: f32) -> vec4<f32> {
    let x = clamp(v, 0.0, 1.0) * 255.0;
    let hi = floor(x);
    return vec4<f32>(hi / 255.0, x - hi, 0.0, 1.0);
}

// table of the previous pass at z (linear interpolation; constant 0 / 1 outside [-Z, Z])
fn lut(z: f32, zr: f32, dim: vec2<i32>) -> f32 {
    if (z <= -zr) {
        return 0.0;
    }
    if (z >= zr) {
        return 1.0;
    }
    let n = nLut(dim);
    let fk = (z + zr) / (2.0 * zr) * f32(n - 1);
    let k0 = clamp(i32(floor(fk)), 0, n - 1);
    let k1 = min(k0 + 1, n - 1);
    let t0 = textureLoad(srcTex, vec2<i32>(k0 % dim.x, k0 / dim.x), 0);
    let t1 = textureLoad(srcTex, vec2<i32>(k1 % dim.x, k1 / dim.x), 0);
    let v0 = (round(t0.r * 255.0) + t0.g) / 255.0;
    let v1 = (round(t1.r * 255.0) + t1.g) / 255.0;
    return mix(v0, v1, fk - f32(k0));
}

// blur along one axis of the table function: sum_u K(u) f(z + u * step)
fn blurred(z: f32, stepz: f32, first: bool, zr: f32, dim: vec2<i32>, d: vec2<f32>) -> f32 {
    let r = rho();
    let s0 = -(3.0 * floor(r) + 3.0);
    let taps = i32(-2.0 * s0 + 1.0);
    let stride = max(1, (taps + MAXT - 1) / MAXT);
    var acc = 0.0;
    var wsum = 0.0;
    for (var j = 0; j < taps; j = j + stride) {
        let u = s0 + f32(j + stride / 2);
        let w = kernelAt(u, r, s0);
        if (w != 0.0) {
            var v: f32;
            if (first) {
                v = coverage(z + u * stepz, d);
            } else {
                v = lut(z + u * stepz, zr, dim);
            }
            acc = acc + w * v;
            wsum = wsum + w;
        }
    }
    return acc / max(wsum, 1e-20);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dim = vec2<i32>(textureDimensions(srcTex, 0));
    let p = vec2<i32>(floor(in.pos.xy));
    let keep = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0).a * 0.0;   // keeps binding 1 in the auto layout
    let d = dirv();
    let zr = halfSpan(d);
    let pi = i32(round(P.passIndex));
    if (pi <= 1) {
        let n = nLut(dim);
        let k = p.y * dim.x + p.x;
        if (k >= n) {
            return vec4<f32>(keep, 0.0, 0.0, 1.0);
        }
        let z = -zr + 2.0 * zr * f32(k) / f32(max(n - 1, 1));
        if (pi == 0) {
            return pack16(blurred(z, d.x, true, zr, dim, d) + keep);
        }
        return pack16(blurred(z, d.y, false, zr, dim, d) + keep);
    }
    let r = P.layerRect;   // the layer, not the canvas (grown by fxPad when another effect of the stack spills out)
    let tmin = min(r.x * d.x, r.z * d.x) + min(r.y * d.y, r.w * d.y);
    let tmax = max(r.x * d.x, r.z * d.x) + max(r.y * d.y, r.w * d.y);
    let e = tmin + clamp(P.completion, 0.0, 100.0) / 100.0 * (tmax - tmin);
    let g = clamp(lut(dot(in.pos.xy, d) - e, zr, dim), 0.0, 1.0);
    let o = textureLoad(origTex, clamp(p, vec2<i32>(0), dim - vec2<i32>(1)), 0);
    let a8 = round(o.a * 255.0);
    let na = floor(a8 * g + 0.5);
    var c8 = vec3<f32>(0.0);
    if (o.a > 0.0) {
        c8 = clamp(round(o.rgb / o.a * 255.0), vec3<f32>(0.0), vec3<f32>(255.0));
    }
    return vec4<f32>(floor((c8 * na + 127.0) / 255.0) / 255.0, na / 255.0);
}
