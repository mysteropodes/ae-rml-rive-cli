// After Effects "CC Glue Gun" (CC Glue Gun) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// One glue blob at Brush Position: a disc of Stroke Width px, lit as a glossy bump (Light / Reflection), refracting the
// layer under it by Strength %. In After Effects the brush is animated and leaves a trail over Time Span: the node
// has no history of frames, so only the blob at the current position is drawn. Positions assumed.
struct Params {
    size: vec2<f32>,
    brush: vec2<f32>,     // AE 1 Brush Position (layer px)
    width: f32,           // AE 2 Stroke Width (px)
    density: f32,         // AE 3 Density
    timeSpan: f32,        // AE 4 Time Span (sec)
    reflection: f32,      // AE 5 Reflection (%)
    strength: f32,        // AE 6 Strength (%)
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
    let q = layerPos(in.uv);
    let s = tapL(q);
    let r = max(P.width, 1.0) * 0.5;
    let v = (q - layerPt(P.brush)) / r;
    let d2 = dot(v, v);
    if (d2 >= 1.0) {
        return s;
    }
    let z = sqrt(1.0 - d2);
    let nrm = vec3<f32>(v, z);
    let src = q - v * r * (1.0 - z) * clamp(P.strength / 100.0, 0.0, 2.0);
    let t = tapL(src);
    let L = normalize(vec3<f32>(-0.5, -0.6, 0.62));
    let spec = pow(max(dot(normalize(L + vec3<f32>(0.0, 0.0, 1.0)), nrm), 0.0), 40.0) * clamp(P.reflection / 100.0, 0.0, 1.0);
    let shade = 0.75 + 0.25 * max(dot(nrm, L), 0.0);
    let c = straight8(t) * shade + vec3<f32>(spec);
    let edge = smoothstep(1.0, 0.9, d2);
    let a = max(t.a, edge);
    return out8(mix(straight8(s), c, edge), max(s.a, a));
}
