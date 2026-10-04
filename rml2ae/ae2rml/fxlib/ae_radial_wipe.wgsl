// After Effects "Radial Wipe" (ADBE Radial Wipe) — UNVERIFIED: written from the effect's definition, not yet
// measured against After Effects renders. Angles as in AE (0 = up, clockwise). The wiped sector starts at Start Angle
// and covers Completion x 360 degrees: clockwise (Wipe 1), counterclockwise (2) or half each way (3, Both). Its edges
// are anti-aliased over one pixel; Feather widens that ramp to Feather pixels (distance to the edge ray). The alpha of
// the layer is multiplied by the matte, rounded to 8 bits.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 3 Wipe Center (layer px)
    completion: f32,      // AE 1 Transition Completion (%)
    startAngle: f32,      // AE 2 Start Angle (degrees)
    wipe: f32,            // AE 4 Wipe: 1 Clockwise, 2 Counterclockwise, 3 Both
    feather: f32,         // AE 5 Feather (px)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
    pad2: f32,
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

// signed distance (degrees, positive outside) of angle x to the arc centred on m with half-width h
fn arcDist(x: f32, m: f32, h: f32) -> f32 {
    let dx = abs(x - m - 360.0 * round((x - m) / 360.0));
    return dx - h;
}

// AE angle of v (degrees): 0 = up, clockwise (y down)
fn aeAngle(v: vec2<f32>) -> f32 {
    return degrees(atan2(v.x, -v.y));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let w = clamp(P.completion, 0.0, 100.0) * 3.6;
    if (w <= 0.0) {
        return s;
    }
    if (w >= 360.0) {
        return vec4<f32>(0.0);
    }
    let v = vec2<f32>(ip) + vec2<f32>(0.5) - P.layerRect.xy - P.center;
    let x = aeAngle(v) - P.startAngle;
    var m = 0.5 * w;                          // clockwise: [0, w]
    let mode = i32(round(P.wipe));
    if (mode == 2) {
        m = -0.5 * w;                         // counterclockwise: [-w, 0]
    } else if (mode == 3) {
        m = 0.0;                              // both: [-w/2, w/2]
    }
    let dDeg = arcDist(x, m, 0.5 * w);
    let r = length(v);
    let dPx = sign(dDeg) * r * sin(radians(min(abs(dDeg), 90.0)));
    let matte = clamp(dPx / max(P.feather, 1.0) + 0.5, 0.0, 1.0);
    let c = straight8(s);
    return out8(c, s.a * matte);
}
