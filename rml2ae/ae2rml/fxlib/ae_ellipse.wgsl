// After Effects "Ellipse" (ADBE Ellipse) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A ring of Thickness px on the ellipse Width x Height around Center, Softness (raw 0..1) of the thickness feathering
// both edges; colour (and its alpha) from Inside Color (inner edge) to Outside Color (outer edge) across the ring. Composite On Original
// draws it over the layer, otherwise alone.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 1 Center (layer px)
    width: f32,           // AE 2 Width (px)
    height: f32,          // AE 3 Height (px)
    thickness: f32,       // AE 4 Thickness (px)
    softness: f32,        // AE 5 Softness (raw 0..1)
    inside: vec4<f32>,    // AE 6 Inside Color
    outside: vec4<f32>,   // AE 7 Outside Color
    composite: f32,       // AE 8 Composite On Original
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let v = layerPos(in.uv) - layerPt(P.center);
    let rad = max(vec2<f32>(P.width, P.height) * 0.5, vec2<f32>(1.0));
    let e = length(v / rad);                                       // 1 on the ellipse
    let dpx = (e - 1.0) * min(rad.x, rad.y);                       // approx. signed distance (px)
    let half = max(P.thickness * 0.5, 0.5);
    let soft = max(P.softness * half, 0.5);
    let cov = clamp((half - abs(dpx)) / soft + 0.5, 0.0, 1.0);
    let t = clamp(dpx / (2.0 * half) + 0.5, 0.0, 1.0);
    let col = mix(P.inside.rgb, P.outside.rgb, t);
    let k = cov * clamp(mix(P.inside.a, P.outside.a, t), 0.0, 1.0);   // AE's default colours carry an alpha
    let ep = vec4<f32>(col * k, k);
    if (P.composite > 0.5) {
        return round((ep + s * (1.0 - k)) * 255.0) / 255.0;
    }
    return round(ep * 255.0) / 255.0;
}
