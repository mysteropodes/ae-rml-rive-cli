// After Effects "Wave Warp" (ADBE Wave Warp) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A wave of Wave Width px travelling along Direction (AE angle) displaces the source perpendicular to it by
// Wave Height px * wave(t), t = position along Direction / Width + Phase / 360. Wave Speed (time) is not modelled:
// this is the frame at time 0. Pinning and Antialiasing are ignored. Bilinear, transparent outside the layer.
struct Params {
    size: vec2<f32>,
    kind: f32,            // AE 1 Wave Type (menu)
    height: f32,          // AE 2 Wave Height (px)
    width: f32,           // AE 3 Wave Width (px)
    direction: f32,       // AE 4 Direction (degrees)
    speed: f32,           // AE 5 Wave Speed (not modelled)
    pinning: f32,         // AE 6 Pinning (not modelled)
    phase: f32,           // AE 7 Phase (degrees)
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

const TAU: f32 = 6.283185307;

// AE Wave Type 1 Sine, 2 Square, 3 Triangle, 4 Sawtooth, 5 Circle, 6 Semicircle, 7 Uncircle (8/9 noise: sine here)
fn wave(t: f32, kind: i32) -> f32 {
    let f = fract(t);
    switch kind {
        case 2: { return select(-1.0, 1.0, f < 0.5); }
        case 3: { return 1.0 - 4.0 * abs(f - 0.5); }
        case 4: { return 2.0 * f - 1.0; }
        case 5: {
            let u = 4.0 * fract(t * 2.0) - 1.0;
            let s = sqrt(max(1.0 - (u - 1.0) * (u - 1.0) * 0.25, 0.0));
            return select(-s, s, f < 0.5);
        }
        case 6: {
            let u = 2.0 * f - 1.0;
            return sqrt(max(1.0 - u * u, 0.0)) * 2.0 - 1.0;
        }
        case 7: {
            let u = 2.0 * f - 1.0;
            return 1.0 - sqrt(max(1.0 - u * u, 0.0)) * 2.0;
        }
        default: { return sin(TAU * t); }
    }
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let p = layerPos(in.uv);
    let dir = aeDir(P.direction);
    let n = vec2<f32>(-dir.y, dir.x);                 // perpendicular (90 deg -> vertical)
    let t = dot(p, dir) / max(P.width, 1.0) + P.phase / 360.0;
    let q = p - n * P.height * wave(t, i32(round(P.kind)));
    return round(tapL(q) * 255.0) / 255.0;
}
