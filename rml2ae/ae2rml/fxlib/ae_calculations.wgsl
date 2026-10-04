// After Effects "Calculations" (ADBE Calculations) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Input Channel and Second Layer Channel: 1 RGBA, 2 Gray, 3 Red, 4 Green, 5 Blue, 6 Alpha (a single channel shows as
// grey); each can be inverted. The second layer, at Second Layer Opacity %, is blended over the input with Blending
// Mode (separable modes, menu order assumed); Preserve Transparency keeps the input's alpha.
struct Params {
    size: vec2<f32>,
    inChan: f32,          // AE 1 Input Channel (menu)
    inInvert: f32,        // AE 2 Invert Input
    secChan: f32,         // AE 4 Second Layer Channel (menu)
    secOpacity: f32,      // AE 5 Second Layer Opacity (%)
    secInvert: f32,       // AE 6 Invert Second Layer
    stretch: f32,         // AE 7 Stretch Second Layer to Fit
    mode: f32,            // AE 8 Blending Mode (menu)
    preserve: f32,        // AE 9 Preserve Transparency
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
@group(0) @binding(3) var mapTex: texture_2d<f32>;

// the second layer at layer px q: the generated Rive node renders it at the layer's size, so it is stretched over the
// layer (AE "If Layer Sizes Differ" has nothing left to decide); transparent outside the layer
fn mapAt(q: vec2<f32>) -> vec4<f32> {
    let sz = layerSize();
    if (q.x < 0.0 || q.y < 0.0 || q.x >= sz.x || q.y >= sz.y) {
        return vec4<f32>(0.0);
    }
    let md = vec2<i32>(textureDimensions(mapTex, 0));
    let mp = clamp(vec2<i32>(floor(q * vec2<f32>(md) / sz)), vec2<i32>(0), md - vec2<i32>(1));
    return textureLoad(mapTex, mp, 0);
}

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

fn chan(t: vec4<f32>, m: i32, inv: bool) -> vec4<f32> {
    var c = straight8(t);
    var a = t.a;
    if (m == 2) {
        c = vec3<f32>(dot(c, vec3<f32>(0.299, 0.587, 0.114)));
    } else if (m == 3) {
        c = vec3<f32>(c.r);
    } else if (m == 4) {
        c = vec3<f32>(c.g);
    } else if (m == 5) {
        c = vec3<f32>(c.b);
    } else if (m == 6) {
        c = vec3<f32>(a);
        a = 1.0;
    }
    if (inv) {
        c = vec3<f32>(1.0) - c;
    }
    return vec4<f32>(c, a);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let o = mapAt(layerPos(in.uv));
    let b = chan(s, i32(round(P.inChan)), P.inInvert > 0.5);
    let t = chan(o, i32(round(P.secChan)), P.secInvert > 0.5);
    let k = clamp(P.secOpacity / 100.0, 0.0, 1.0) * t.a;
    let r = mix(b.rgb, blendRGB(b.rgb, t.rgb, i32(round(P.mode))), k);
    var a = b.a;
    if (P.preserve < 0.5) {
        a = b.a + k * (1.0 - b.a);
    }
    return out8(r, a);
}
