// After Effects "CC Jaws" (CC Jaws) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The layer splits along the line through Center perpendicular to Direction (AE angle) with a toothed edge (Shape
// 1 Spikes: triangular teeth Width px apart, Height (0..1) of Width tall), and the two halves slide apart along Direction
// by Completion (0..1) of the layer diagonal. Shapes RoboJaw, Block, Waves are not modelled (Spikes used).
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 2 Center (layer px)
    completion: f32,      // AE 1 Completion (raw 0..1)
    direction: f32,       // AE 3 Direction (degrees)
    height: f32,          // AE 4 Height (raw 0..1, fraction of Width)
    width: f32,           // AE 5 Width (px)
    shape: f32,           // AE 6 Shape (menu)
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
    let p = layerPos(in.uv);
    let c = layerPt(P.center);
    let dir = aeDir(P.direction);
    let tng = vec2<f32>(-dir.y, dir.x);
    let shift = clamp(P.completion, 0.0, 1.0) * length(layerSize());
    let w = max(P.width, 2.0);
    let amp = P.height * w * 0.5;
    // which half does p belong to after the move? test both: half A moves +dir, half B moves -dir
    let qa = p - dir * shift * 0.5;
    let qb = p + dir * shift * 0.5;
    let ta = dot(qa - c, tng) / w;
    let tb = dot(qb - c, tng) / w;
    let edgeA = amp * (1.0 - 2.0 * abs(fract(ta) - 0.5)) * 2.0 - amp;
    let edgeB = amp * (1.0 - 2.0 * abs(fract(tb) - 0.5)) * 2.0 - amp;
    if (dot(qa - c, dir) > edgeA) {
        return round(tapL(qa) * 255.0) / 255.0;
    }
    if (dot(qb - c, dir) <= edgeB) {
        return round(tapL(qb) * 255.0) / 255.0;
    }
    return vec4<f32>(0.0);
}
