// After Effects "Compound Arithmetic" (ADBE Compound Arithmetic) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Operator on 8-bit values (this layer A, second source B): 1 Copy, 2 Add, 3 Subtract, 4 Multiply, 5 Difference,
// 6 And, 7 Or, 8 Xor, 9 Lighten, 10 Darken, 11 Minimum, 12 Maximum, 13 Screen, 14 Overlay, 15 Hard Light. Operate
// On Channels 1 RGB, 2 ARGB, 3 Alpha. Overflow 1 Clip, 2 Wrap (modulo 256), 3 Scale (the operator's range mapped
// to 0..255). Blend With Original % mixes the result back with the layer.
struct Params {
    size: vec2<f32>,
    op: f32,              // AE 2 Operator (menu)
    channels: f32,        // AE 3 Operate On Channels (menu)
    overflow: f32,        // AE 4 Overflow Behavior (menu)
    stretch: f32,         // AE 5 Stretch Second Source to Fit
    original: f32,        // AE 6 Blend With Original (%)
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

fn op1(a: f32, b: f32, op: i32) -> vec2<f32> {      // (result, scale range low..high packed as low = -k, used by Scale)
    switch op {
        case 2: { return vec2<f32>(a + b, 0.0); }
        case 3: { return vec2<f32>(a - b, 1.0); }
        case 4: { return vec2<f32>(a * b / 255.0, 2.0); }
        case 5: { return vec2<f32>(abs(a - b), 2.0); }
        case 6: { return vec2<f32>(f32(u32(a) & u32(b)), 2.0); }
        case 7: { return vec2<f32>(f32(u32(a) | u32(b)), 2.0); }
        case 8: { return vec2<f32>(f32(u32(a) ^ u32(b)), 2.0); }
        case 9, 12: { return vec2<f32>(max(a, b), 2.0); }
        case 10, 11: { return vec2<f32>(min(a, b), 2.0); }
        case 13: { return vec2<f32>(255.0 - (255.0 - a) * (255.0 - b) / 255.0, 2.0); }
        case 14: {
            if (a < 128.0) { return vec2<f32>(2.0 * a * b / 255.0, 2.0); }
            return vec2<f32>(255.0 - 2.0 * (255.0 - a) * (255.0 - b) / 255.0, 2.0);
        }
        case 15: {
            if (b < 128.0) { return vec2<f32>(2.0 * a * b / 255.0, 2.0); }
            return vec2<f32>(255.0 - 2.0 * (255.0 - a) * (255.0 - b) / 255.0, 2.0);
        }
        default: { return vec2<f32>(b, 2.0); }
    }
}

fn apply1(a: f32, b: f32) -> f32 {
    let r = op1(round(a * 255.0), round(b * 255.0), i32(round(P.op)));
    let ov = i32(round(P.overflow));
    var v = r.x;
    if (ov == 2) {
        v = v - 256.0 * floor(v / 256.0);
    } else if (ov == 3) {
        if (r.y == 0.0) { v = v * 0.5; }                     // add: 0..510
        else if (r.y == 1.0) { v = (v + 255.0) * 0.5; }     // subtract: -255..255
    }
    return clamp(v, 0.0, 255.0) / 255.0;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let o = mapAt(layerPos(in.uv));
    let c = straight8(s);
    let oc = straight8(o);
    let ch = i32(round(P.channels));
    var r = c;
    var a = s.a;
    if (ch != 3) {
        r = vec3<f32>(apply1(c.r, oc.r), apply1(c.g, oc.g), apply1(c.b, oc.b));
    }
    if (ch != 1) {
        a = apply1(s.a, o.a);
    }
    let k = 1.0 - clamp(P.original / 100.0, 0.0, 1.0);
    return out8(mix(c, r, k), mix(s.a, a, k));
}
