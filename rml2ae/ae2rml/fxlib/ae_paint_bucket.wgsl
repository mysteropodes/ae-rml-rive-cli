// After Effects "Paint Bucket" (ADBE Paint Bucket) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A flood fill from Fill Point over the pixels similar to it (Fill Selector, assumed menu: 1 Color & Alpha, 2 Straight
// Color, 3 Transparency, 4 Opacity, 5 Alpha Channel; Tolerance 0..255), built in 24 passes: pass 0 seeds the point,
// each pass extends the region along 8 directions up to 24 px while the pixels stay similar (so it follows the
// connected region; very winding regions may stay partly unfilled), the last pass paints Color at Opacity %
// (Invert Fill paints the rest). View Threshold shows the region in white. Stroke options (antialias, feather, choke)
// and blending modes other than Normal are not modelled. Positions assumed.
struct Params {
    size: vec2<f32>,
    point: vec2<f32>,     // AE 1 Fill Point (layer px)
    color: vec4<f32>,     // AE 8 Color
    selector: f32,        // AE 2 Fill Selector (menu)
    tolerance: f32,       // AE 3 Tolerance (0..255)
    viewThr: f32,         // AE 4 View Threshold
    invert: f32,          // AE 7 Invert Fill
    opacity: f32,         // AE 9 Opacity (%)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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
@group(0) @binding(3) var origTex: texture_2d<f32>;

fn orig(p: vec2<i32>) -> vec4<f32> {
    return textureLoad(origTex, p, 0);
}

fn similar(p: vec2<i32>, refc: vec4<f32>) -> bool {
    let t = orig(p);
    let tol = P.tolerance / 255.0 + 0.5 / 255.0;
    switch i32(round(P.selector)) {
        case 2: { return all(abs(straight8(t) - straight8(refc)) <= vec3<f32>(tol)); }
        case 3: { return t.a <= tol; }
        case 4: { return t.a > tol; }
        case 5: { return abs(t.a - refc.a) <= tol; }
        default: { return all(abs(t - refc) <= vec4<f32>(tol)); }
    }
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dim = vec2<i32>(textureDimensions(origTex, 0));
    let ip = pixelOf(in.uv);
    let sp = clamp(vec2<i32>(floor(P.point)), vec2<i32>(0), dim - vec2<i32>(1));     // Fill Point in canvas px
    let refc = orig(sp);
    let pidx = i32(round(P.passIndex));
    let mine = similar(ip, refc);
    var m = 0.0;
    if (pidx == 0) {
        m = select(0.0, 1.0, all(ip == sp) && mine);
    } else {
        m = textureLoad(srcTex, ip, 0).r;
    }
    if (pidx < 23 && m < 0.5 && mine) {
        // walk 8 rays from this pixel through similar pixels: reaching a filled one fills this one
        let dirs = array<vec2<i32>, 8>(vec2<i32>(1, 0), vec2<i32>(-1, 0), vec2<i32>(0, 1), vec2<i32>(0, -1),
                                       vec2<i32>(1, 1), vec2<i32>(-1, -1), vec2<i32>(1, -1), vec2<i32>(-1, 1));
        for (var d = 0; d < 8; d++) {
            var q = ip;
            for (var k = 0; k < 24; k++) {
                q = q + dirs[d];
                if (q.x < 0 || q.y < 0 || q.x >= dim.x || q.y >= dim.y) { break; }
                if (pidx > 0 && textureLoad(srcTex, q, 0).r > 0.5) { m = 1.0; break; }
                if (pidx == 0 && all(q == sp)) { m = 1.0; break; }
                if (!similar(q, refc)) { break; }
            }
            if (m > 0.5) { break; }
        }
    }
    if (pidx < 23) {
        return vec4<f32>(m, 0.0, 0.0, 1.0);
    }
    var fill = m > 0.5;
    if (P.invert > 0.5) { fill = !fill; }
    let o = orig(ip);
    if (P.viewThr > 0.5) {
        return vec4<f32>(vec3<f32>(select(0.0, 1.0, fill)), 1.0);
    }
    if (!fill) {
        return o;
    }
    let op = clamp(P.opacity / 100.0, 0.0, 1.0);
    let c = P.color.rgb * op + o.rgb * (1.0 - op);
    let a = op + o.a * (1.0 - op);
    return round(vec4<f32>(c, a) * 255.0) / 255.0;
}
