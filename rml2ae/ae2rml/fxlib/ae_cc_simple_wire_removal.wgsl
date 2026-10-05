// After Effects "CC Simple Wire Removal" (CC Simple Wire Removal) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A band of Thickness px along the line Point A -> Point B is rebuilt from outside it: Removal Style 1 Fade blends
// the two band edges across the band, 3 Displace copies the content from one Thickness further along the normal,
// 4 Displace Horizontal from Thickness further along x; 2 Frame Offset (another time) falls back to Fade. Slope % softens
// the band edges; Mirror Blend % mixes in the content mirrored across the line.
struct Params {
    size: vec2<f32>,
    a: vec2<f32>,         // AE 1 Point A (layer px)
    b: vec2<f32>,         // AE 2 Point B (layer px)
    style: f32,           // AE 3 Removal Style (menu)
    thickness: f32,       // AE 4 Thickness (px)
    slope: f32,           // AE 5 Slope (raw 0..1)
    mirror: f32,          // AE 6 Mirror Blend (raw 0..1)
    frameOffset: f32,     // AE 7 Frame Offset
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let A = layerPt(P.a);
    let ab = layerPt(P.b) - A;
    let u = ab / max(length(ab), 1e-3);
    let n = vec2<f32>(-u.y, u.x);
    let d = dot(q - A, n);
    let hw = max(P.thickness * 0.5, 0.0);
    let s = tapL(q);
    if (hw <= 0.0 || abs(d) >= hw) {
        return s;
    }
    let base = q - n * d;
    let st = i32(round(P.style));
    var r: vec4<f32>;
    if (st == 3) {
        r = tapL(q + n * select(-1.0, 1.0, d >= 0.0) * P.thickness);
    } else if (st == 4) {
        r = tapL(q + vec2<f32>(select(-1.0, 1.0, d * n.x >= 0.0) * P.thickness, 0.0));
    } else {
        let lo = tapL(base - n * (hw + 1.0));
        let hi = tapL(base + n * (hw + 1.0));
        r = mix(lo, hi, (d + hw) / (2.0 * hw));
    }
    r = mix(r, tapL(q - 2.0 * n * d), clamp(P.mirror, 0.0, 1.0));
    let edge = clamp(P.slope, 0.0, 1.0) * hw;
    var w = 1.0;
    if (edge > 0.0) {
        w = clamp((hw - abs(d)) / edge, 0.0, 1.0);
    }
    return round(mix(s, r, w) * 255.0) / 255.0;
}
