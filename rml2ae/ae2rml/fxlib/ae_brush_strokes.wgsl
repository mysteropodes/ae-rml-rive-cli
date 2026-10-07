// After Effects "Brush Strokes" (ADBE Brush Strokes) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Repaints the layer with strokes: on a grid of spacing Brush Size x 2 / Stroke Density, each stroke (jittered by
// Stroke Randomness, its angle too) is a segment of Stroke Length px and Brush Size px wide along Stroke Angle,
// painted with the layer's colour at its centre; the last stroke covering a pixel wins. Paint Surface 1 on the original
// image, 2 on transparent, 3 on white, 4 on black; Blend With Original %. AE's random strokes differ.
struct Params {
    size: vec2<f32>,
    angle: f32,           // AE 1 Stroke Angle (degrees)
    brush: f32,           // AE 2 Brush Size (px)
    length: f32,          // AE 3 Stroke Length (px)
    density: f32,         // AE 4 Stroke Density
    randomness: f32,      // AE 5 Stroke Randomness
    surface: f32,         // AE 6 Paint Surface (menu)
    original: f32,        // AE 7 Blend With Original (raw 0..1)
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

fn h2(c: vec2<f32>, k: f32) -> f32 {
    let q = fract(vec3<f32>(c.x, c.y, k) * vec3<f32>(0.1031, 0.1030, 0.0973));
    let r = q + dot(q, q.yzx + vec3<f32>(33.33));
    return fract((r.x + r.y) * r.z);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let s = tapL(q);
    let sp = max(P.brush * 2.0 / max(P.density, 0.1), 2.0);
    let rad = max(P.brush, 1.0) * 0.5;
    let len = max(P.length, 1.0);
    let reach = i32(clamp(ceil((len * 0.5 + rad) / sp), 1.0, 4.0));
    let cell = floor(q / sp);
    var best = -1.0;
    var col = vec4<f32>(0.0);
    let rnd = clamp(P.randomness, 0.0, 2.0);
    for (var j = -reach; j <= reach; j++) {
        for (var i = -reach; i <= reach; i++) {
            let cc = cell + vec2<f32>(f32(i), f32(j));
            let ctr = (cc + vec2<f32>(0.5) + (vec2<f32>(h2(cc, 1.0), h2(cc, 2.0)) - vec2<f32>(0.5)) * rnd) * sp;
            let a = radians(P.angle + (h2(cc, 3.0) - 0.5) * 60.0 * rnd);
            let dir = vec2<f32>(cos(a), -sin(a));
            let v = q - ctr;
            let along = clamp(dot(v, dir), -0.5 * len, 0.5 * len);
            let d = length(v - dir * along);
            let order = h2(cc, 4.0);
            if (d <= rad && order > best) {
                best = order;
                col = tapL(ctr);
            }
        }
    }
    var base = s;
    let surf = i32(round(P.surface));
    if (surf == 2) { base = vec4<f32>(0.0); }
    else if (surf == 3) { base = vec4<f32>(1.0); }
    else if (surf == 4) { base = vec4<f32>(0.0, 0.0, 0.0, 1.0); }
    var o = base;
    if (best >= 0.0) {
        o = col + base * (1.0 - col.a);
    }
    o = mix(o, s, clamp(P.original, 0.0, 1.0));
    return round(o * 255.0) / 255.0;
}
