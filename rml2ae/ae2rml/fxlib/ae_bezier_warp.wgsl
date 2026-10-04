// After Effects "Bezier Warp" (ADBE BEZMESH) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The layer's four edges become cubic Bezier curves through the 4 vertices and 8 tangents; the inside follows the
// Coons patch of the four curves. Each output pixel is inverse-mapped by Newton iterations on (u, v) from a bilinear
// guess. Quality is not used (the patch is evaluated exactly).
struct Params {
    size: vec2<f32>,
    tlV: vec2<f32>,           // AE 1 Top Left Vertex (layer px)
    tlT: vec2<f32>,           // AE 2 Top Left Tangent (layer px)
    trT: vec2<f32>,           // AE 3 Top Right Tangent (layer px)
    trV: vec2<f32>,           // AE 4 Right Top Vertex (layer px)
    rtT: vec2<f32>,           // AE 5 Right Top Tangent (layer px)
    rbT: vec2<f32>,           // AE 6 Right Bottom Tangent (layer px)
    brV: vec2<f32>,           // AE 7 Bottom Right Vertex (layer px)
    brT: vec2<f32>,           // AE 8 Bottom Right Tangent (layer px)
    blT: vec2<f32>,           // AE 9 Bottom Left Tangent (layer px)
    blV: vec2<f32>,           // AE 10 Left Bottom Vertex (layer px)
    lbT: vec2<f32>,           // AE 11 Left Bottom Tangent (layer px)
    ltT: vec2<f32>,           // AE 12 Left Top Tangent (layer px)
    quality: f32,         // AE 13 Quality
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

fn bez(a: vec2<f32>, b: vec2<f32>, c: vec2<f32>, d: vec2<f32>, t: f32) -> vec2<f32> {
    let s = 1.0 - t;
    return s * s * s * a + 3.0 * s * s * t * b + 3.0 * s * t * t * c + t * t * t * d;
}

fn coons(u: f32, v: f32) -> vec2<f32> {
    let tl = layerPt(P.tlV);
    let tr = layerPt(P.trV);
    let br = layerPt(P.brV);
    let bl = layerPt(P.blV);
    let top = bez(tl, layerPt(P.tlT), layerPt(P.trT), tr, u);
    let bot = bez(bl, layerPt(P.blT), layerPt(P.brT), br, u);
    let lft = bez(tl, layerPt(P.ltT), layerPt(P.lbT), bl, v);
    let rgt = bez(tr, layerPt(P.rtT), layerPt(P.rbT), br, v);
    let bil = (1.0 - u) * (1.0 - v) * tl + u * (1.0 - v) * tr + u * v * br + (1.0 - u) * v * bl;
    return (1.0 - v) * top + v * bot + (1.0 - u) * lft + u * rgt - bil;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let sz = layerSize();
    var uv = clamp(q / sz, vec2<f32>(0.0), vec2<f32>(1.0));
    let h = 1e-3;
    for (var i = 0; i < 12; i++) {
        let f = coons(uv.x, uv.y) - q;
        let ju = (coons(uv.x + h, uv.y) - coons(uv.x - h, uv.y)) / (2.0 * h);
        let jv = (coons(uv.x, uv.y + h) - coons(uv.x, uv.y - h)) / (2.0 * h);
        let det = ju.x * jv.y - ju.y * jv.x;
        if (abs(det) < 1e-6) {
            break;
        }
        let du = (f.x * jv.y - f.y * jv.x) / det;
        let dv = (ju.x * f.y - ju.y * f.x) / det;
        uv = uv - vec2<f32>(du, dv);
    }
    let err = length(coons(uv.x, uv.y) - q);
    if (err > 0.5 || uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) {
        return vec4<f32>(0.0);
    }
    return outL(uv * sz);
}
