// After Effects "CC Threads" (CC Threads) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Weaves the layer into threads: two families of threads Width / Height px apart, turned by Direction around Center;
// each thread covers Coverage % of its spacing and takes the layer's colour along its centre line; Overlaps (threads
// crossed before passing under) sets the weave; Shadowing % darkens a thread where it goes under and its edges; Texture
// % adds a thin striping. Gaps between threads are transparent.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 5 Center (layer px)
    width: f32,           // AE 1 Width (px)
    height: f32,          // AE 2 Height (px)
    overlaps: f32,        // AE 3 Overlaps
    direction: f32,       // AE 4 Direction (degrees)
    coverage: f32,        // AE 6 Coverage (%)
    shadowing: f32,       // AE 7 Shadowing (%)
    texture: f32,         // AE 8 Texture (%)
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

fn rot(v: vec2<f32>, a: f32) -> vec2<f32> {
    return vec2<f32>(v.x * cos(a) - v.y * sin(a), v.x * sin(a) + v.y * cos(a));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let c = layerPt(P.center);
    let ang = radians(P.direction);
    let p = rot(q - c, -ang);
    let sp = max(vec2<f32>(P.width, P.height), vec2<f32>(2.0));
    let cell = floor(p / sp);
    let f = p / sp - cell;
    let cov = clamp(P.coverage / 100.0, 0.05, 1.0);
    let inH = abs(f.y - 0.5) < 0.5 * cov;                   // horizontal thread of this row
    let inV = abs(f.x - 0.5) < 0.5 * cov;                   // vertical thread of this column
    if (!inH && !inV) {
        return vec4<f32>(0.0);
    }
    let ov = max(round(P.overlaps), 1.0);
    let hOnTop = (i32(floor((cell.x + cell.y) / ov)) & 1) == 0;
    var useH = inH;
    if (inH && inV) {
        useH = hOnTop;
    }
    var src: vec2<f32>;
    var across: f32;
    var under = false;
    if (useH) {
        src = vec2<f32>(p.x, (cell.y + 0.5) * sp.y);
        across = abs(f.y - 0.5) / (0.5 * cov);
        under = inV && !hOnTop;
    } else {
        src = vec2<f32>((cell.x + 0.5) * sp.x, p.y);
        across = abs(f.x - 0.5) / (0.5 * cov);
        under = inH && hOnTop;
    }
    let t = tapL(c + rot(src, ang));
    var k = 1.0 - clamp(P.shadowing / 100.0, 0.0, 1.0) * (0.6 * across * across + select(0.0, 0.4, under));
    let stripe = 0.5 + 0.5 * sin(select(p.x, p.y, useH) * 3.0);
    k = k * (1.0 - clamp(P.texture / 100.0, 0.0, 1.0) * 0.3 * stripe);
    return round(vec4<f32>(t.rgb * k, t.a) * 255.0) / 255.0;
}
