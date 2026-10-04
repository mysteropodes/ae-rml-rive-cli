// After Effects "CC Composite" (CC Composite) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Parameter positions measured. Composites the layer as it was before its effects (layerTex, texture role "layer")
// with what the effects above produced: Composite Original (assumed menu, 41 entries) 1 In Front (original over the
// result), 2 Behind (original under it), 3 Normal (the original alone), 4.. the separable blend modes in the order
// Add, Darken, Multiply, Color Burn, Linear Burn, Lighten, Screen, Color Dodge, Linear Dodge, Overlay, Soft Light,
// Hard Light, Linear Light, Vivid Light, Pin Light, Hard Mix, Difference, Exclusion (others: Normal). Opacity % of the
// original; RGB Only keeps the result's alpha.
struct Params {
    size: vec2<f32>,
    opacity: f32,         // AE 1 Opacity (%)
    mode: f32,            // AE 2 Composite Original (menu)
    rgbOnly: f32,         // AE 3 RGB Only
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
@group(0) @binding(3) var layerTex: texture_2d<f32>;

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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let r = textureLoad(srcTex, ip, 0);                     // the effects' result
    let o = textureLoad(layerTex, ip, 0);                   // the layer before them
    let op = clamp(P.opacity / 100.0, 0.0, 1.0);
    let mode = i32(round(P.mode));
    var out = r;
    if (mode == 1) {
        out = o * op + r * (1.0 - o.a * op);
    } else if (mode == 2) {
        out = r + o * op * (1.0 - r.a);
    } else if (mode == 3) {
        out = mix(r, o, op);
    } else {
        let m = mode - 2;                                    // 2.. in blend1's numbering (2 = Add ...)
        let rc = straight8(r);
        let oc = straight8(o);
        let b = blendRGB(rc, oc, m);
        let a = r.a;
        out = vec4<f32>(mix(rc, b, op * o.a) * a, a);
    }
    if (P.rgbOnly > 0.5) {
        let c = select(vec3<f32>(0.0), out.rgb / out.a, out.a > 0.0);
        out = vec4<f32>(c * r.a, r.a);
    }
    return round(clamp(out, vec4<f32>(0.0), vec4<f32>(1.0)) * 255.0) / 255.0;
}
