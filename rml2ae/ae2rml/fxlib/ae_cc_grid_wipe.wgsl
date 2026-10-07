// After Effects "CC Grid Wipe" (CC Grid Wipe) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A grid of Tiles cells across the layer width, turned by Rotation around Center, hides the layer as Completion
// grows: each cell shrinks to nothing (Shape 1 Doors: from its sides inward on x, 2 Radial: a disc, 3 Rectangular:
// a square), the cells near Center first. Border feathers the cell edges (px; AE's range is 30..200, default 75, so its unit is still a guess). Reverse Transition inverts it.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 2 Center (layer px)
    completion: f32,      // AE 1 Completion (raw 0..1)
    rotation: f32,        // AE 3 Rotation (degrees)
    border: f32,          // AE 4 Border (px)
    tiles: f32,           // AE 5 Tiles
    shape: f32,           // AE 6 Shape (menu)
    reverse: f32,         // AE 7 Reverse Transition
    passIndex: f32,
    pad0: f32,
    pad1: f32,
    pad2: f32,
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
    let p = layerPos(in.uv);
    let sz = layerSize();
    let c = layerPt(P.center);
    let a = radians(P.rotation);
    let d = p - c;
    let r = vec2<f32>(d.x * cos(a) + d.y * sin(a), -d.x * sin(a) + d.y * cos(a));
    let cell = sz.x / max(round(P.tiles), 1.0);
    let ci = floor(r / cell);
    let lp = (r / cell - ci) * 2.0 - vec2<f32>(1.0);            // -1..1 inside the cell
    let cc = (ci + vec2<f32>(0.5)) * cell;
    let far = length(sz);
    let delay = clamp(length(cc) / far, 0.0, 1.0);
    let t = clamp(P.completion * 2.0 - delay, 0.0, 1.0);    // this cell's own completion
    let shape = i32(round(P.shape));
    var m: f32;
    if (shape == 1) {
        m = abs(lp.x);
    } else if (shape == 2) {
        m = length(lp) / 1.41421356;
    } else {
        m = max(abs(lp.x), abs(lp.y));
    }
    let edge = 1.0 - t;                                            // visible while m < edge
    let fw = max(P.border, 0.5) / (0.5 * cell);
    var v = clamp((edge - m) / fw + 0.5, 0.0, 1.0);
    if (t >= 1.0) {
        v = 0.0;
    }
    if (P.reverse > 0.5) {
        v = 1.0 - v;
    }
    return round(s * v * 255.0) / 255.0;
}
