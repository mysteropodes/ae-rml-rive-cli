// After Effects "CC Glass" (CC Glass) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The layer's own Property (1 Alpha, 2 Red, 3 Green, 4 Blue, 5 Luminance; assumed menu) is a height field, smoothed
// over Softness px, raised by Height %: the content is refracted along the surface slope by Displacement % and lit
// (Light Intensity %, Light Color, Light Height, Light Direction) with Ambient / Diffuse / Specular / Roughness.
// A separate Bump Map layer, AE lights, Light Type and Metal are not modelled. Positions assumed (groups counted).
struct Params {
    size: vec2<f32>,
    lightColor: vec4<f32>,// AE 10 Light Color
    property: f32,        // AE 3 Property (menu)
    softness: f32,        // AE 4 Softness (px)
    height: f32,          // AE 5 Height (%)
    displacement: f32,    // AE 6 Displacement (%)
    intensity: f32,       // AE 9 Light Intensity (%)
    lightHeight: f32,     // AE 12 Light Height
    lightDir: f32,        // AE 14 Light Direction (degrees)
    ambient: f32,         // AE 16 Ambient
    diffuse: f32,         // AE 17 Diffuse
    specular: f32,        // AE 18 Specular
    roughness: f32,       // AE 19 Roughness
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
fn luma(c: vec3<f32>) -> f32 { return dot(c, vec3<f32>(0.299, 0.587, 0.114)); }

fn h1(q: vec2<f32>) -> f32 {
    let t = tapL(q);
    let c = straight8(t);
    switch i32(round(P.property)) {
        case 2: { return c.r * t.a; }
        case 3: { return c.g * t.a; }
        case 4: { return c.b * t.a; }
        case 5: { return luma(c) * t.a; }
        default: { return t.a; }
    }
}

fn hs(q: vec2<f32>) -> f32 {
    let r = max(P.softness, 0.0) * 0.5;
    return (h1(q) + h1(q + vec2<f32>(r, 0.0)) + h1(q - vec2<f32>(r, 0.0)) + h1(q + vec2<f32>(0.0, r)) + h1(q - vec2<f32>(0.0, r))) / 5.0;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let e = 1.0 + max(P.softness, 0.0) * 0.25;
    let g = vec2<f32>(hs(q + vec2<f32>(e, 0.0)) - hs(q - vec2<f32>(e, 0.0)), hs(q + vec2<f32>(0.0, e)) - hs(q - vec2<f32>(0.0, e))) / (2.0 * e);
    let hgt = P.height / 100.0 * 20.0;
    let nrm = normalize(vec3<f32>(-g * hgt, 1.0));
    let src = q - nrm.xy * P.displacement / 100.0 * 20.0;
    let t = tapL(src);
    let d = aeDir(P.lightDir);
    let lh = clamp(P.lightHeight / 100.0, -1.0, 1.0);
    let L = normalize(vec3<f32>(d * (1.0 - abs(lh)), max(lh, 0.0) + 0.3));
    let diff = max(dot(nrm, L), 0.0);
    let hv = normalize(L + vec3<f32>(0.0, 0.0, 1.0));
    let shin = mix(64.0, 4.0, clamp(P.roughness / 0.2, 0.0, 1.0));
    let spec = pow(max(dot(nrm, hv), 0.0), shin) * P.specular / 100.0;
    let li = P.intensity / 100.0;
    let c = straight8(t) * (P.ambient / 100.0 + P.diffuse / 100.0 * diff * li) + P.lightColor.rgb * spec * li;
    return out8(c, t.a);
}
