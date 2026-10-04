// After Effects "CC HexTile" (CC HexTile) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Hexagonal tiles of Radius px on a grid through Center, turned by Rotate: each tile shows the layer around Center
// (the same patch in every tile); Smearing % blends each tile towards the layer as it is under the tile. Render
// (assumed 1 = tiles) is not used.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 3 Center (layer px)
    render: f32,          // AE 1 Render
    radius: f32,          // AE 2 Radius (px)
    rotate: f32,          // AE 4 Rotate (degrees)
    smearing: f32,        // AE 5 Smearing (%)
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

// centre of the hexagon (pointy-top axial grid of size r) containing p
fn hexCenter(p: vec2<f32>, r: f32) -> vec2<f32> {
    let qf = (0.57735027 * p.x - 0.33333333 * p.y) / r;
    let rf = (0.66666667 * p.y) / r;
    let sf = -qf - rf;
    var q = round(qf);
    var rr = round(rf);
    let s = round(sf);
    let dq = abs(q - qf);
    let dr = abs(rr - rf);
    let ds = abs(s - sf);
    if (dq > dr && dq > ds) {
        q = -rr - s;
    } else if (dr > ds) {
        rr = -q - s;
    }
    return vec2<f32>(r * 1.73205081 * (q + rr * 0.5), r * 1.5 * rr);
}

fn rot(v: vec2<f32>, a: f32) -> vec2<f32> {
    return vec2<f32>(v.x * cos(a) - v.y * sin(a), v.x * sin(a) + v.y * cos(a));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let c = layerPt(P.center);
    let a = radians(P.rotate);
    let p = rot(q - c, -a);
    let hc = hexCenter(p, max(P.radius, 1.0));
    let local = rot(p - hc, a);
    let tile = c + local;                                    // the patch around Center
    let src = mix(tile, q, clamp(P.smearing / 100.0, 0.0, 1.0));
    return round(tapL(src) * 255.0) / 255.0;
}
