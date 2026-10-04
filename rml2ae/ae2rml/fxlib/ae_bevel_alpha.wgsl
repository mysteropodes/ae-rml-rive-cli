// After Effects "Bevel Alpha" (ADBE Bevel Alpha) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// The alpha, box-blurred over Edge Thickness px (at most 8), gives a height field; its gradient lit from Light Angle
// (AE angle) shades the colour: highlights towards Light Color, shadows towards black, by Light Intensity. Alpha kept.
struct Params {
    size: vec2<f32>,
    thickness: f32,       // AE 1 Edge Thickness (px)
    angle: f32,           // AE 2 Light Angle (degrees)
    color: vec4<f32>,     // AE 3 Light Color
    intensity: f32,       // AE 4 Light Intensity (0..1)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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

fn alphaAt(p: vec2<i32>) -> f32 {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    if (any(p < vec2<i32>(0)) || any(p >= dimi)) {
        return 0.0;
    }
    return textureLoad(srcTex, p, 0).a;
}

fn boxA(p: vec2<i32>, r: i32) -> f32 {
    var acc = 0.0;
    for (var y = -r; y <= r; y++) {
        for (var x = -r; x <= r; x++) {
            acc += alphaAt(p + vec2<i32>(x, y));
        }
    }
    return acc / f32((2 * r + 1) * (2 * r + 1));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let r = clamp(i32(round(P.thickness)), 1, 8);
    let gx = boxA(ip + vec2<i32>(1, 0), r) - boxA(ip - vec2<i32>(1, 0), r);
    let gy = boxA(ip + vec2<i32>(0, 1), r) - boxA(ip - vec2<i32>(0, 1), r);
    let a = radians(P.angle);
    let l = vec2<f32>(sin(a), -cos(a));                  // towards the light
    let shade = clamp(dot(vec2<f32>(gx, gy), l) * f32(r), -1.0, 1.0) * clamp(P.intensity, 0.0, 1.0);
    let c = straight8(s);
    var o = c;
    if (shade > 0.0) {
        o = mix(c, P.color.rgb, shade);
    } else {
        o = c * (1.0 + shade);
    }
    return out8(o, s.a);
}
