// After Effects "3D Glasses" (ADBE 3D Glasses2) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Combines a left view (this layer) and a right view (the Right View layer), the right one shifted by Scene
// Convergence px horizontally and Vertical Alignment px vertically; Swap Left-Right exchanges them. 3D View (assumed
// menu): 1 Stereo Pair (side by side), 2 Interlace Upper L Lower R, 3 Red Green LR, 4 Red Blue LR, 5 Balanced Colored
// Red Blue, 6 Balanced Red Green (Balance mixes the grey of each view into its colour channel).
struct Params {
    size: vec2<f32>,
    convergence: f32,     // AE 3 Scene Convergence (px)
    vertical: f32,        // AE 4 Vertical Alignment (px)
    swap: f32,            // AE 6 Swap Left-Right
    view: f32,            // AE 7 3D View (menu)
    balance: f32,         // AE 8 Balance
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

fn grey(t: vec4<f32>) -> f32 {
    return dot(straight8(t), vec3<f32>(0.299, 0.587, 0.114)) * t.a;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let sz = layerSize();
    let view = i32(round(P.view));
    let sh = vec2<f32>(P.convergence, P.vertical);
    var L = tapL(q);
    var R = mapAt(q - sh);
    if (view == 1) {          // side by side: left half = left view squeezed, right half = right view squeezed
        let u = vec2<f32>(fract(q.x / sz.x * 2.0) * sz.x, q.y);
        L = tapL(u);
        R = mapAt(u - sh);
        if (P.swap > 0.5) { let t = L; L = R; R = t; }
        return round(select(L, R, q.x >= sz.x * 0.5) * 255.0) / 255.0;
    }
    if (P.swap > 0.5) { let t = L; L = R; R = t; }
    if (view == 2) {
        return round(select(R, L, (i32(floor(q.y)) & 1) == 0) * 255.0) / 255.0;
    }
    let gl = grey(L);
    let gr = grey(R);
    let k = clamp(P.balance / 10.0, 0.0, 1.0);
    var c = vec3<f32>(gl, gr, 0.0);
    if (view == 4) {
        c = vec3<f32>(gl, 0.0, gr);
    } else if (view == 5) {
        let cl = straight8(L) * L.a;
        let cr = straight8(R) * R.a;
        c = vec3<f32>(mix(cl.r, gl, k), cr.g, mix(cr.b, gr, k));
    } else if (view == 6) {
        c = vec3<f32>(mix(straight8(L).r * L.a, gl, k), mix(straight8(R).g * R.a, gr, k), 0.0);
    }
    return out8(c, max(L.a, R.a));
}
