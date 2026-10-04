// After Effects "Warp" (ADBE WRPMESH, Photoshop's warp styles) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Warp Style (assumed menu): 1 Arc, 2 Arc Lower, 3 Arc Upper, 4 Arch, 5 Bulge, 6 Shell Lower, 7 Shell Upper, 8 Flag,
// 9 Wave, 10 Fish, 11 Rise, 12 Fisheye, 13 Inflate, 14 Squeeze, 15 Twist; Warp Axis 1 Horizontal, 2 Vertical; Bend
// -100..100; Horizontal / Vertical Distortion -100..100 (a perspective-like scale across the layer). Approximate
// forms of Photoshop's envelopes, inverse-mapped: the match name and the exact curves are to be measured.
struct Params {
    size: vec2<f32>,
    style: f32,           // AE 1 Warp Style (menu)
    axis: f32,            // AE 2 Warp Axis (menu)
    bend: f32,            // AE 3 Bend
    hDist: f32,           // AE 4 Horizontal Distortion
    vDist: f32,           // AE 5 Vertical Distortion
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

fn outL(q: vec2<f32>) -> vec4<f32> {
    return round(tapL(q) * 255.0) / 255.0;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let sz = layerSize();
    let q = layerPos(in.uv);
    var p = q / sz * 2.0 - vec2<f32>(1.0);          // -1..1
    let vert = i32(round(P.axis)) == 2;
    if (vert) {
        p = p.yx;
    }
    let b = clamp(P.bend / 100.0, -1.0, 1.0);
    let st = i32(round(P.style));
    var s = p;                                      // source, in the same -1..1 space
    switch st {
        case 1: { s.y = p.y + b * (1.0 - p.x * p.x) * 0.5 * (1.0 + p.y) * 0.5 - b * (1.0 - p.x * p.x) * 0.5; }
        case 2: { s.y = p.y + b * (1.0 - p.x * p.x) * 0.5 * clamp(p.y, 0.0, 1.0); }
        case 3: { s.y = p.y + b * (1.0 - p.x * p.x) * 0.5 * clamp(-p.y, 0.0, 1.0); }
        case 4: { s.y = p.y + b * (1.0 - p.x * p.x) * 0.5; }
        case 5: { s.y = p.y / (1.0 + b * 0.5 * (1.0 - p.x * p.x)); }
        case 6: { s.y = p.y / (1.0 + b * 0.5 * (1.0 - p.x * p.x) * clamp(p.y, 0.0, 1.0)); }
        case 7: { s.y = p.y / (1.0 + b * 0.5 * (1.0 - p.x * p.x) * clamp(-p.y, 0.0, 1.0)); }
        case 8: { s.y = p.y + b * 0.25 * sin(3.14159265 * p.x); }
        case 9: { s.y = p.y + b * 0.25 * sin(3.14159265 * p.x) * (0.5 + 0.5 * p.y); }
        case 10: { s.y = p.y + b * 0.25 * sin(3.14159265 * p.x) * p.y; }
        case 11: { s.y = p.y - b * 0.25 * p.x; }
        case 12: { let r = length(p); s = p * mix(1.0, pow(max(r, 1e-4), b), 1.0) / max(r, 1e-4) * r; s = p * pow(max(r, 1e-4) / 1.41421356, b * 0.5); }
        case 13: { s = p * (1.0 - b * 0.25 * (1.0 - p * p).yx); }
        case 14: { s.x = p.x * (1.0 + b * 0.5 * (1.0 - p.y * p.y)); }
        case 15: {
            let r = length(p);
            let a = b * 3.14159265 * 0.5 * max(1.0 - r / 1.41421356, 0.0);
            s = vec2<f32>(p.x * cos(a) - p.y * sin(a), p.x * sin(a) + p.y * cos(a));
        }
        default: {}
    }
    // distortions: a scale across the layer (perspective-like)
    s.y = s.y / max(1.0 + clamp(P.hDist / 100.0, -0.95, 0.95) * s.x * 0.5, 0.05);
    s.x = s.x / max(1.0 + clamp(P.vDist / 100.0, -0.95, 0.95) * s.y * 0.5, 0.05);
    if (vert) {
        s = s.yx;
    }
    return outL((s + vec2<f32>(1.0)) * 0.5 * sz);
}
