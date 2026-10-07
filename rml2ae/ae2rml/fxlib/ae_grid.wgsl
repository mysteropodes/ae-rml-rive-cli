// After Effects "Grid" (ADBE Grid) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Lines of Border px on a grid of cells anchored at Anchor; the cell is Width x Height px (Size From 2 Width Slider:
// square cells of Width; 3 Width & Height; 1 Corner Point: the cell spans Anchor..Corner). Invert Grid swaps lines
// and cells. Blending Mode 1 None draws the grid alone, otherwise it is drawn over the layer at Opacity. Feather is
// not modelled (lines anti-aliased over one pixel). Positions checked against AE 26 (Feather = 7-10).
struct Params {
    size: vec2<f32>,
    anchor: vec2<f32>,    // AE 1 Anchor (layer px)
    corner: vec2<f32>,    // AE 3 Corner (layer px)
    sizeFrom: f32,        // AE 2 Size From (menu)
    width: f32,           // AE 4 Width (px)
    height: f32,          // AE 5 Height (px)
    border: f32,          // AE 6 Border (px)
    color: vec4<f32>,     // AE 12 Color
    invert: f32,          // AE 11 Invert Grid
    opacity: f32,         // AE 13 Opacity (%)
    blendMode: f32,       // AE 14 Blending Mode (1 None)
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

fn lineCov(x: f32, cell: f32, border: f32) -> f32 {
    let m = x - cell * floor(x / cell);                // 0..cell
    let d = min(m, cell - m);                          // distance to the nearest line centre
    return clamp(border * 0.5 - d + 0.5, 0.0, 1.0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let a = layerPt(P.anchor);
    var cell = vec2<f32>(max(P.width, 1.0));
    let mode = i32(round(P.sizeFrom));
    if (mode == 3) {
        cell = max(vec2<f32>(P.width, P.height), vec2<f32>(1.0));
    } else if (mode == 1) {
        cell = max(abs(layerPt(P.corner) - a), vec2<f32>(1.0));
    }
    let v = layerPos(in.uv) - a;
    var g = max(lineCov(v.x, cell.x, P.border), lineCov(v.y, cell.y, P.border));
    if (P.invert > 0.5) {
        g = 1.0 - g;
    }
    let k = g * clamp(P.opacity / 100.0, 0.0, 1.0);
    let gp = vec4<f32>(P.color.rgb * k, k);
    if (i32(round(P.blendMode)) == 1) {
        return round(gp * 255.0) / 255.0;
    }
    return round((gp + s * (1.0 - k)) * 255.0) / 255.0;
}
