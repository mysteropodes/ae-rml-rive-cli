// After Effects "CC Bend It" (CC Bend It) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The layer is bent along an arc from Start towards End: the segment Start -> End (length L) becomes an arc of total
// angle Bend (degrees) that keeps its length; points off the axis keep their distance to it. Before Start, Render
// Prestart 1 None leaves nothing, 2 Static keeps the layer, 3 Bend continues the arc. Past End the arc goes on.
struct Params {
    size: vec2<f32>,
    bend: f32,            // AE 1 Bend (degrees)
    prestart: f32,        // AE 4 Render Prestart (menu)
    start: vec2<f32>,     // AE 2 Start (layer px)
    end: vec2<f32>,       // AE 3 End (layer px)
    distort: f32,         // AE 5 Distort (menu)
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let S = layerPt(P.start);
    let E = layerPt(P.end);
    let ax = E - S;
    let L = max(length(ax), 1e-3);
    let u = ax / L;
    let n = vec2<f32>(-u.y, u.x);
    let theta = radians(P.bend);
    let along = dot(q - S, u);
    let pre = i32(round(P.prestart));
    if (along < 0.0 && pre != 3) {
        if (pre == 2) {
            return tapL(q);
        }
        return vec4<f32>(0.0);
    }
    if (abs(theta) < 1e-4) {
        return tapL(q);
    }
    let R = L / theta;                       // signed radius: centre on the n side for a positive bend
    let C = S + n * R;
    let v = q - C;
    let r0 = -n * R;                          // C -> S
    let ang = atan2(r0.x * v.y - r0.y * v.x, dot(r0, v));
    var phi = ang * sign(R);
    if (pre != 3 && phi < 0.0) {
        phi += 6.2831853;
    }
    let s = abs(R) * phi;
    let d = (abs(R) - length(v)) * sign(R);
    return tapL(S + u * s + n * d);
}
