// After Effects "Photo Filter" (ADBE Photo Filter) — UNVERIFIED: written from the effect's definition (the
// Photoshop Photo Filter), not yet measured against After Effects renders.
// The straight colour is multiplied by the filter colour at Density: c' = mix(c, c * f, Density / 100); with Preserve
// Luminosity the luminosity of c is restored (W3C SetLum, Lum = .3 .59 .11, ClipColor). Filter presets 1..20 use the
// Photoshop filter colours below; any other menu index uses the Color parameter (Custom). The menu index of Custom is
// to be measured. Alpha untouched; 8-bit rounding, premultiplied.
struct Params {
    size: vec2<f32>,
    filterType: f32,      // AE 1 Filter (menu)
    density: f32,         // AE 3 Density (%)
    color: vec4<f32>,     // AE 2 Color (straight 0..1), used by Custom
    preserve: f32,        // AE 4 Preserve Luminosity (checkbox)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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

fn presetColor(i: i32) -> vec3<f32> {
    var c = array<vec3<f32>, 20>(
        vec3<f32>(236.0, 138.0, 0.0), vec3<f32>(250.0, 150.0, 0.0), vec3<f32>(235.0, 177.0, 19.0),   // Warming 85, LBA, 81
        vec3<f32>(0.0, 109.0, 255.0), vec3<f32>(0.0, 93.0, 255.0), vec3<f32>(0.0, 181.0, 255.0),     // Cooling 80, LBB, 82
        vec3<f32>(234.0, 26.0, 26.0), vec3<f32>(243.0, 132.0, 23.0), vec3<f32>(249.0, 227.0, 28.0),  // Red, Orange, Yellow
        vec3<f32>(25.0, 201.0, 25.0), vec3<f32>(29.0, 203.0, 234.0), vec3<f32>(29.0, 53.0, 234.0),   // Green, Cyan, Blue
        vec3<f32>(155.0, 29.0, 234.0), vec3<f32>(227.0, 24.0, 227.0), vec3<f32>(172.0, 122.0, 51.0), // Violet, Magenta, Sepia
        vec3<f32>(255.0, 0.0, 0.0), vec3<f32>(0.0, 34.0, 205.0), vec3<f32>(0.0, 140.0, 0.0),         // Deep Red, Blue, Emerald
        vec3<f32>(255.0, 213.0, 0.0), vec3<f32>(0.0, 194.0, 177.0));                                   // Deep Yellow, Underwater
    if (i >= 1 && i <= 20) {
        let k = i - 1;
        return c[k] / 255.0;
    }
    return P.color.rgb;
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
        o = vec3<f32>(l) + (o - vec3<f32>(l)) * l / max(l - n, 1e-6);
    }
    if (x > 1.0) {
        o = vec3<f32>(l) + (o - vec3<f32>(l)) * (1.0 - l) / max(x - l, 1e-6);
    }
    return o;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let f = presetColor(i32(round(P.filterType)));
    var o = mix(c, c * f, clamp(P.density / 100.0, 0.0, 1.0));
    if (P.preserve > 0.5) {
        o = clipColor(o + vec3<f32>(lum(c) - lum(o)));
    }
    return out8(o, s.a);
}
