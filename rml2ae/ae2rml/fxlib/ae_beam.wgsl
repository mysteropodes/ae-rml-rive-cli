// After Effects "Beam" (ADBE Laser) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A beam along Starting Point -> Ending Point: the visible part is [Time - Length, Time] of the segment (fractions
// 0..1 from Length % and Time %), its thickness goes from Starting to Ending Thickness, Inside Color in the core and
// Outside Color at the rim, with Softness % of the half thickness feathered. 3D Perspective is not modelled.
// Composite On Original draws it over the layer, otherwise alone.
struct Params {
    size: vec2<f32>,
    a: vec2<f32>,         // AE 1 Starting Point (layer px)
    b: vec2<f32>,         // AE 2 Ending Point (layer px)
    len: f32,             // AE 3 Length (raw 0..1)
    time: f32,            // AE 4 Time (raw 0..1)
    t0: f32,              // AE 5 Starting Thickness (px)
    t1: f32,              // AE 6 Ending Thickness (px)
    inside: vec4<f32>,    // AE 8 Inside Color
    outside: vec4<f32>,   // AE 9 Outside Color
    softness: f32,        // AE 7 Softness (raw 0..1)
    composite: f32,       // AE 11 Composite On Original
    passIndex: f32,
    pad0: f32,
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
    let p = layerPos(in.uv);
    let a = layerPt(P.a);
    let ab = layerPt(P.b) - a;
    let L2 = max(dot(ab, ab), 1e-6);
    let u = dot(p - a, ab) / L2;                       // 0 at the start, 1 at the end
    let te = clamp(P.time, 0.0, 1.0);
    let ts = max(te - clamp(P.len, 0.0, 1.0), 0.0);
    let uc = clamp(u, ts, te);
    let d = length(p - (a + ab * uc));
    let half = 0.5 * mix(P.t0, P.t1, uc);
    let soft = max(P.softness * half, 0.5);
    let cov = clamp((half - d) / soft + 0.5, 0.0, 1.0) * select(0.0, 1.0, te > ts);
    let col = mix(P.inside.rgb, P.outside.rgb, clamp(d / max(half, 1e-3), 0.0, 1.0));
    let bp = vec4<f32>(col * cov, cov);
    if (P.composite > 0.5) {
        return round((bp + s * (1.0 - cov)) * 255.0) / 255.0;
    }
    return round(bp * 255.0) / 255.0;
}
