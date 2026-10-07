// After Effects "Eyedropper Fill" (match name assumed ADBE Sample Fill) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Fills the layer with the average colour of a disc of Sample Radius px around Sample Point (24 x 24 samples over the
// disc). Average Pixel Colors (assumed menu): 1 Skip Empty (transparent samples ignored), 2 All, 3 All Premultiplied,
// 4 Including Alpha. Maintain Original Alpha keeps the layer's alpha; Blend With Original % mixes the layer back.
struct Params {
    size: vec2<f32>,
    point: vec2<f32>,     // AE 1 Sample Point (layer px)
    radius: f32,          // AE 2 Sample Radius (px)
    average: f32,         // AE 3 Average Pixel Colors (menu)
    keepAlpha: f32,       // AE 4 Maintain Original Alpha
    original: f32,        // AE 5 Blend With Original (raw 0..1)
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
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = layerPt(P.point);
    let r = max(P.radius, 0.0);
    let mode = i32(round(P.average));
    var acc = vec4<f32>(0.0);
    var n = 0.0;
    for (var j = 0; j < 24; j++) {
        for (var i = 0; i < 24; i++) {
            let o = (vec2<f32>(f32(i), f32(j)) + vec2<f32>(0.5)) / 24.0 * 2.0 - vec2<f32>(1.0);
            if (dot(o, o) > 1.0) { continue; }
            let t = tapL(c + o * r);
            if (mode == 1 && t.a <= 0.0) { continue; }
            if (mode == 3 || mode == 4) {
                acc += t;
            } else {
                acc += vec4<f32>(straight8(t), t.a);
            }
            n += 1.0;
        }
    }
    var col = vec3<f32>(0.0);
    var a = 1.0;
    if (n > 0.0) {
        let m = acc / n;
        col = m.rgb;
        if (mode == 3 || mode == 4) {
            col = select(vec3<f32>(0.0), m.rgb / m.a, m.a > 0.0);
        }
        if (mode == 4) {
            a = m.a;
        }
    }
    if (P.keepAlpha > 0.5) {
        a = s.a;
    }
    let k = clamp(P.original, 0.0, 1.0);
    return out8(mix(col, straight8(s), k), mix(a, s.a, k));
}
