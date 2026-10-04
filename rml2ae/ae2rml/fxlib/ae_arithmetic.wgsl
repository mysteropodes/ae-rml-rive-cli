// After Effects "Arithmetic" (ADBE Arithmetic) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Per channel, on the straight 8-bit value v and the operand k (Red/Green/Blue Value, 0..255): Operator 1 And,
// 2 Or, 3 Xor, 4 Add, 5 Subtract, 6 Difference, 7 Min, 8 Max, 9 Block Above (v > k -> 0), 10 Block Below (v < k -> 0),
// 11 Slice (v >= k -> 255, else 0), 12 Multiply (v*k/255), 13 Screen. Clip Result Values clamps (else wraps mod 256).
struct Params {
    size: vec2<f32>,
    op: f32,              // AE 1 Operator (menu)
    rv: f32,              // AE 2 Red Value
    gv: f32,              // AE 3 Green Value
    bv: f32,              // AE 4 Blue Value
    clip: f32,            // AE 5 Clip Result Values (checkbox)
    passIndex: f32,
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

fn opOne(v: i32, k: i32, op: i32) -> i32 {
    switch op {
        case 1: { return v & k; }
        case 2: { return v | k; }
        case 3: { return v ^ k; }
        case 4: { return v + k; }
        case 5: { return v - k; }
        case 6: { return abs(v - k); }
        case 7: { return min(v, k); }
        case 8: { return max(v, k); }
        case 9: { return select(v, 0, v > k); }
        case 10: { return select(v, 0, v < k); }
        case 11: { return select(0, 255, v >= k); }
        case 12: { return (v * k + 127) / 255; }
        case 13: { return 255 - ((255 - v) * (255 - k) + 127) / 255; }
        default: { return v; }
    }
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = vec3<i32>(round(straight8(s) * 255.0));
    let k = vec3<i32>(round(vec3<f32>(P.rv, P.gv, P.bv)));
    let op = i32(round(P.op));
    var o = vec3<i32>(opOne(c.r, k.r, op), opOne(c.g, k.g, op), opOne(c.b, k.b, op));
    if (P.clip > 0.5) {
        o = clamp(o, vec3<i32>(0), vec3<i32>(255));
    } else {
        o = ((o % vec3<i32>(256)) + vec3<i32>(256)) % vec3<i32>(256);
    }
    return out8(vec3<f32>(o) / 255.0, s.a);
}
