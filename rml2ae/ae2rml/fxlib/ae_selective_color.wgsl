// After Effects "Selective Color" (ADBE Selective Color) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Photoshop's Selective Color: each of nine colour classes (Reds .. Blacks) gets its own Cyan / Magenta / Yellow /
// Black adjustment (%). A pixel's weight in a class: Reds/Greens/Blues = max - middle channel when that channel is the
// largest; Cyans/Magentas/Yellows = middle - min when the opposite channel is the smallest; Whites = 2 (min - 0.5);
// Blacks = 2 (0.5 - max); Neutrals = 1 - |max - 0.5| - |min - 0.5|. Each ink c = 1 - channel moves by adj x weight,
// scaled by c (Method 1 Relative) or not (2 Absolute); Black moves all three. Parameter positions assumed: the
// Details group, one group of 4 per class starting at 9.
struct Params {
    size: vec2<f32>,
    method: f32,          // AE 1 Method (menu)
    redC: f32,          // AE 9 Reds Cyan (%)
    redM: f32,          // AE 10 Reds Magenta (%)
    redY: f32,          // AE 11 Reds Yellow (%)
    redK: f32,          // AE 12 Reds Black (%)
    yelC: f32,          // AE 14 Yellows Cyan (%)
    yelM: f32,          // AE 15 Yellows Magenta (%)
    yelY: f32,          // AE 16 Yellows Yellow (%)
    yelK: f32,          // AE 17 Yellows Black (%)
    greC: f32,          // AE 19 Greens Cyan (%)
    greM: f32,          // AE 20 Greens Magenta (%)
    greY: f32,          // AE 21 Greens Yellow (%)
    greK: f32,          // AE 22 Greens Black (%)
    cyaC: f32,          // AE 24 Cyans Cyan (%)
    cyaM: f32,          // AE 25 Cyans Magenta (%)
    cyaY: f32,          // AE 26 Cyans Yellow (%)
    cyaK: f32,          // AE 27 Cyans Black (%)
    bluC: f32,          // AE 29 Blues Cyan (%)
    bluM: f32,          // AE 30 Blues Magenta (%)
    bluY: f32,          // AE 31 Blues Yellow (%)
    bluK: f32,          // AE 32 Blues Black (%)
    magC: f32,          // AE 34 Magentas Cyan (%)
    magM: f32,          // AE 35 Magentas Magenta (%)
    magY: f32,          // AE 36 Magentas Yellow (%)
    magK: f32,          // AE 37 Magentas Black (%)
    whiC: f32,          // AE 39 Whites Cyan (%)
    whiM: f32,          // AE 40 Whites Magenta (%)
    whiY: f32,          // AE 41 Whites Yellow (%)
    whiK: f32,          // AE 42 Whites Black (%)
    neuC: f32,          // AE 44 Neutrals Cyan (%)
    neuM: f32,          // AE 45 Neutrals Magenta (%)
    neuY: f32,          // AE 46 Neutrals Yellow (%)
    neuK: f32,          // AE 47 Neutrals Black (%)
    blaC: f32,          // AE 49 Blacks Cyan (%)
    blaM: f32,          // AE 50 Blacks Magenta (%)
    blaY: f32,          // AE 51 Blacks Yellow (%)
    blaK: f32,          // AE 52 Blacks Black (%)
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

fn adj(cmy: vec3<f32>, a: vec4<f32>, w: f32, rel: bool) -> vec3<f32> {
    if (w <= 0.0) {
        return vec3<f32>(0.0);
    }
    var d = a.xyz / 100.0 + vec3<f32>(a.w / 100.0);
    if (rel) {
        d = d * cmy;
    }
    return d * w;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let mid = c.r + c.g + c.b - mx - mn;
    let cmy = vec3<f32>(1.0) - c;
    let rel = i32(round(P.method)) != 2;
    var d = vec3<f32>(0.0);
    let p = mx - mid;
    let q = mid - mn;
    if (c.r == mx) { d += adj(cmy, vec4<f32>(P.redC, P.redM, P.redY, P.redK), p, rel); }
    if (c.g == mx) { d += adj(cmy, vec4<f32>(P.greC, P.greM, P.greY, P.greK), p, rel); }
    if (c.b == mx) { d += adj(cmy, vec4<f32>(P.bluC, P.bluM, P.bluY, P.bluK), p, rel); }
    if (c.b == mn) { d += adj(cmy, vec4<f32>(P.yelC, P.yelM, P.yelY, P.yelK), q, rel); }
    if (c.r == mn) { d += adj(cmy, vec4<f32>(P.cyaC, P.cyaM, P.cyaY, P.cyaK), q, rel); }
    if (c.g == mn) { d += adj(cmy, vec4<f32>(P.magC, P.magM, P.magY, P.magK), q, rel); }
    d += adj(cmy, vec4<f32>(P.whiC, P.whiM, P.whiY, P.whiK), 2.0 * (mn - 0.5), rel);
    d += adj(cmy, vec4<f32>(P.blaC, P.blaM, P.blaY, P.blaK), 2.0 * (0.5 - mx), rel);
    d += adj(cmy, vec4<f32>(P.neuC, P.neuM, P.neuY, P.neuK), 1.0 - abs(mx - 0.5) - abs(mn - 0.5), rel);
    return out8(vec3<f32>(1.0) - (cmy + d), s.a);
}
