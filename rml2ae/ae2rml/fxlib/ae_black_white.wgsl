// After Effects "Black & White" (ADBE Black&White, FR "Noir et blanc") — Photoshop's B&W mixer. Measured vs AE 2026
// (8 bpc), on the STRAIGHT 8-bit colour:
//   sort the channels max >= mid >= min; the colour = min (grey) + (mid - min) x a SECONDARY (the complement of the
//   min channel: min R -> Cyans, min G -> Magentas, min B -> Yellows) + (max - mid) x a PRIMARY (the max channel:
//   Reds / Greens / Blues); gray = min + (mid - min) * secondary% / 100 + (max - mid) * primary% / 100, clamped 0..255,
//   rounded to 8 bits (exact .5 ties go DOWN in AE: 114.5 -> 114).  Bit-exact on the default setting.
//   Tint (AE 7 = 1): the 8-bit grey is coloured like a "Color" blend of the Tint Color (W3C/PDF SetLum + ClipColor,
//   luma 0.30/0.59/0.11): C = tint + (gray - lum(tint)), then chroma scaled about gray until it fits 0..1. Exact in
//   the midtones; in the clipped shadows / highlights AE differs by 1/255 on one channel (see manifest notes).
// Alpha untouched; fully transparent pixels stay 0.
struct Params {
    size: vec2<f32>,
    reds: f32,            // AE 1 Reds %     (-200..300, default 40)
    yellows: f32,         // AE 2 Yellows %  (default 60)
    greens: f32,          // AE 3 Greens %   (default 40)
    cyans: f32,           // AE 4 Cyans %    (default 60)
    blues: f32,           // AE 5 Blues %    (default 20)
    magentas: f32,        // AE 6 Magentas % (default 80)
    tintOn: f32,          // AE 7 Tint checkbox 0/1
    pad0: f32,
    pad1: f32,
    pad2: f32,
    tintColor: vec4<f32>, // AE 8 Tint Color (straight 0..1, alpha ignored)
    passIndex: f32,
    pad3: f32,
    pad4: f32,
    pad5: f32,
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

fn q8(c: vec3<f32>) -> vec3<f32> {
    return floor(clamp(c, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5) / 255.0;
}

fn lum(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.3, 0.59, 0.11));
}

// W3C / PDF "Color" blend: SetLum(tint, l) + ClipColor
fn setLum(c: vec3<f32>, l: f32) -> vec3<f32> {
    var r = c + vec3<f32>(l - lum(c));
    let ll = lum(r);
    let n = min(r.r, min(r.g, r.b));
    let x = max(r.r, max(r.g, r.b));
    if (n < 0.0 && ll - n > 1e-9) {
        r = vec3<f32>(ll) + (r - vec3<f32>(ll)) * (ll / (ll - n));
    }
    let x2 = max(r.r, max(r.g, r.b));
    if (x2 > 1.0 && x2 - ll > 1e-9) {
        r = vec3<f32>(ll) + (r - vec3<f32>(ll)) * ((1.0 - ll) / (x2 - ll));
    }
    return r;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let ip = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0), dims - vec2<i32>(1));
    let keep = textureSampleLevel(srcTex, srcSamp, in.uv, 0.0).a * 0.0;   // keeps binding 1 in the auto layout
    let s = textureLoad(srcTex, ip, 0) + vec4<f32>(keep);  // premultiplied
    if (s.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    let c = floor(clamp(s.rgb / s.a, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5);   // straight 8-bit, 0..255
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let md = c.r + c.g + c.b - mx - mn;
    var wp = P.blues;                       // primary = the max channel
    if (c.r >= c.g && c.r >= c.b) {
        wp = P.reds;
    } else if (c.g >= c.b) {
        wp = P.greens;
    }
    var ws = P.yellows;                     // secondary = complement of the min channel
    if (c.r <= c.g && c.r <= c.b) {
        ws = P.cyans;
    } else if (c.g <= c.b) {
        ws = P.magentas;
    }
    let g = clamp(mn + (md - mn) * ws * 0.01 + (mx - md) * wp * 0.01, 0.0, 255.0);
    let g8 = floor(g + 0.498) / 255.0;      // AE rounds exact halves down
    var rgb = vec3<f32>(g8);
    if (P.tintOn >= 0.5) {
        rgb = q8(setLum(q8(P.tintColor.rgb), g8));
    }
    return vec4<f32>(rgb * s.a, s.a);
}
