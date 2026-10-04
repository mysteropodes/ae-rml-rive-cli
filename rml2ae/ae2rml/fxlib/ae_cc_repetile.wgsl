// After Effects "CC RepeTile" (CC RepeTile) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Grows the layer by Expand Right / Left / Down / Up px and fills the new area with copies of the layer: Tiling
// (assumed menu) 1 Repeat, 2 Checker Flip H, 3 Checker Flip V, 4 Checker Flip, 5 Unfold (mirrored), 6 Twist (every
// other tile turned 180 degrees). The node grows its canvas by the largest expansion (ae2rml fx_grow); outside the
// requested expansion stays transparent. Blend Borders is not modelled.
struct Params {
    size: vec2<f32>,
    right: f32,           // AE 1 Expand Right (px)
    left: f32,            // AE 2 Expand Left (px)
    down: f32,            // AE 3 Expand Down (px)
    up: f32,              // AE 4 Expand Up (px)
    tiling: f32,          // AE 5 Tiling (menu)
    blend: f32,           // AE 6 Blend Borders (%)
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
    let q = layerPos(in.uv);
    let sz = layerSize();
    if (q.x < -P.left || q.y < -P.up || q.x >= sz.x + P.right || q.y >= sz.y + P.down) {
        return vec4<f32>(0.0);
    }
    let ti = floor(q / sz);
    var u = q - ti * sz;                                     // position inside its tile
    let odd = vec2<bool>(abs(ti.x) % 2.0 > 0.5, abs(ti.y) % 2.0 > 0.5);
    let mode = i32(round(P.tiling));
    var fx = false;
    var fy = false;
    if (mode == 2) { fx = odd.y; }
    else if (mode == 3) { fy = odd.x; }
    else if (mode == 4) { fx = odd.x != odd.y; fy = fx; }
    else if (mode == 5) { fx = odd.x; fy = odd.y; }
    else if (mode == 6) { fx = odd.x != odd.y; fy = fx; }
    if (fx) { u.x = sz.x - u.x; }
    if (fy) { u.y = sz.y - u.y; }
    return round(tapL(clamp(u, vec2<f32>(0.5), sz - vec2<f32>(0.5))) * 255.0) / 255.0;
}
