// After Effects "Set Channels" (ADBE Set Channels) — UNVERIFIED: written from the effect's definition, not yet
// measured against After Effects renders. Only the layer itself as source (AE 1/3/5/7 "Source Layer" = None): another
// layer as a source is not supported (ae2rml must leave the effect out in that case).
// Each output channel takes one property of the straight 8-bit colour, by the AE menu index (1..10):
//   1 Red, 2 Green, 3 Blue, 4 Alpha, 5 Luminance (Rec.601 .299 .587 .114), 6 Hue, 7 Lightness, 8 Saturation (HLS),
//   9 Full (1), 10 Off (0).
// The output alpha is the "Set Alpha To" channel; colour rounded to 8 bits, then premultiplied by that alpha.
struct Params {
    size: vec2<f32>,
    red: f32,     // AE 2 Set Red To Source 1's
    green: f32,   // AE 4 Set Green To Source 2's
    blue: f32,    // AE 6 Set Blue To Source 3's
    alpha: f32,   // AE 8 Set Alpha To Source 4's
    passIndex: f32,
    pad0: f32,
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

fn hueOf(c: vec3<f32>) -> f32 {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let d = mx - mn;
    if (d <= 0.0) {
        return 0.0;
    }
    var h: f32;
    if (mx == c.r) {
        h = (c.g - c.b) / d;
    } else if (mx == c.g) {
        h = 2.0 + (c.b - c.r) / d;
    } else {
        h = 4.0 + (c.r - c.g) / d;
    }
    return fract(h / 6.0 + 1.0);
}

fn channelOf(c: vec3<f32>, a: f32, mode: i32) -> f32 {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let l = 0.5 * (mx + mn);
    switch mode {
        case 1: { return c.r; }
        case 2: { return c.g; }
        case 3: { return c.b; }
        case 4: { return a; }
        case 5: { return dot(c, vec3<f32>(0.299, 0.587, 0.114)); }
        case 6: { return hueOf(c); }
        case 7: { return l; }
        case 8: {
            let d = mx - mn;
            if (d <= 0.0) {
                return 0.0;
            }
            if (l <= 0.5) {
                return d / (mx + mn);
            }
            return d / (2.0 - mx - mn);
        }
        case 9: { return 1.0; }
        default: { return 0.0; }
    }
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let c = straight8(s);
    let a = s.a;
    let o = vec3<f32>(channelOf(c, a, i32(round(P.red))), channelOf(c, a, i32(round(P.green))),
                      channelOf(c, a, i32(round(P.blue))));
    return out8(o, channelOf(c, a, i32(round(P.alpha))));
}
