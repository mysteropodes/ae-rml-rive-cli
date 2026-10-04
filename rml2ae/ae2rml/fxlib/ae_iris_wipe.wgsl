// After Effects "Iris Wipe" (ADBE Iris Wipe) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A star (or polygon) around Iris Center is wiped (made transparent): Iris Points outer vertices on Outer Radius,
// alternating with inner vertices on Inner Radius when Use Inner Radius is on (otherwise a regular polygon), turned by
// Rotation (AE angle). Edge anti-aliased over one pixel, widened to Feather pixels.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 1 Iris Center (layer px)
    points: f32,          // AE 2 Iris Points (6..32)
    outer: f32,           // AE 3 Outer Radius (px)
    useInner: f32,        // AE 4 Use Inner Radius (checkbox)
    inner: f32,           // AE 5 Inner Radius (px)
    rotation: f32,        // AE 6 Rotation (degrees)
    feather: f32,         // AE 7 Feather (px)
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

const PI: f32 = 3.14159265;

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let n = max(round(P.points), 3.0);
    let h = PI / n;                                       // half sector
    let R = max(P.outer, 0.0);
    var ri = R * cos(h);                                  // polygon: inner vertex on the edge
    if (P.useInner > 0.5) {
        ri = max(P.inner, 0.0);
    }
    let v = layerPos(in.uv) - layerPt(P.center);
    let ang = atan2(v.x, -v.y) - radians(P.rotation);
    var phi = ang - 2.0 * h * floor(ang / (2.0 * h));    // 0..2h
    if (phi > h) {
        phi = 2.0 * h - phi;
    }
    let q = length(v) * vec2<f32>(cos(phi), sin(phi));
    let A = vec2<f32>(R, 0.0);
    let B = ri * vec2<f32>(cos(h), sin(h));
    let ab = B - A;
    let t = clamp(dot(q - A, ab) / max(dot(ab, ab), 1e-6), 0.0, 1.0);
    let dist = length(q - (A + t * ab));
    let side = ab.x * (q.y - A.y) - ab.y * (q.x - A.x);   // > 0: towards the centre side for this winding
    let inside = side >= 0.0;
    var sd = dist;                                        // positive = outside the iris (kept)
    if (inside) {
        sd = -dist;
    }
    let matte = clamp(sd / max(P.feather, 1.0) + 0.5, 0.0, 1.0);
    return out8(straight8(s), s.a * matte);
}
