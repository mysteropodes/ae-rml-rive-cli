// After Effects "CC Light Wipe" (CC Light Wipe) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A wipe from Center (Shape 1 Doors: a band across Direction; 2 Round: a disc; 3 Square) that grows with Completion
// up to the layer diagonal; the layer is removed inside and a glowing edge (Intensity, Color, or the layer's own
// colour with Color from Source) is added along the front. Reverse Transition keeps the inside instead.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 2 Center (layer px)
    completion: f32,      // AE 1 Completion (%)
    intensity: f32,       // AE 3 Intensity
    shape: f32,           // AE 4 Shape (menu)
    direction: f32,       // AE 5 Direction (degrees)
    color: vec4<f32>,     // AE 7 Color
    fromSource: f32,      // AE 6 Color from Source
    reverse: f32,         // AE 8 Reverse Transition
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
    let v = layerPos(in.uv) - layerPt(P.center);
    let shape = i32(round(P.shape));
    var d: f32;
    if (shape == 2) {
        d = length(v);
    } else if (shape == 3) {
        d = max(abs(v.x), abs(v.y));
    } else {
        d = abs(dot(v, aeDir(P.direction)));
    }
    let R = clamp(P.completion / 100.0, 0.0, 1.0) * length(layerSize());
    var inside = d < R;
    if (P.reverse > 0.5) {
        inside = !inside;
    }
    let c = straight8(s);
    var glowCol = P.color.rgb;
    if (P.fromSource > 0.5) {
        glowCol = c;
    }
    let glow = exp(-abs(d - R) / 12.0) * P.intensity / 100.0 * select(0.0, 1.0, R > 0.0);
    let a = select(s.a, 0.0, inside);
    return out8(c + glowCol * glow, max(a, clamp(glow, 0.0, 1.0) * s.a));
}
