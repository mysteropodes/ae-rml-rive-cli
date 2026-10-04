// After Effects "Color Emboss" (ADBE Color Emboss) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Built on the MEASURED Emboss law (ae_emboss.wgsl): per channel d = S(p + v) - S(p - v) with AE's 8 bpc bilinear
// sample S, v = Relief * (sin θ, -cos θ); Color Emboss is assumed to add Contrast/100 * d to the source colour instead
// of to grey 128. Blend With Original mixes with the source. Alpha kept.
struct Params {
    size: vec2<f32>,
    direction: f32,       // AE 1 Direction (degrees)
    relief: f32,          // AE 2 Relief (px)
    contrast: f32,        // AE 3 Contrast (%)
    blend: f32,           // AE 4 Blend With Original (%)
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

// one texel as 8-bit integers (premultiplied), transparent outside the layer
fn texel(ix: i32, iy: i32) -> vec3<i32> {
    let dim = vec2<i32>(textureDimensions(srcTex, 0));
    if (ix < 0 || iy < 0 || ix >= dim.x || iy >= dim.y) {
        return vec3<i32>(0);
    }
    return vec3<i32>(round(textureLoad(srcTex, vec2<i32>(ix, iy), 0).rgb * 255.0));
}

// AE 8 bpc bilinear sample at a position in pixel-centre coordinates (texel (i, j) at (i, j)): weights in 1/256,
// exact integer sum, result rounded half up to 8 bits (measured: 99.9 % of the pixels bit-exact)
fn bilin(p: vec2<f32>) -> vec3<i32> {
    let f = floor(p);
    let t = vec2<i32>(round((p - f) * 256.0));
    let i = vec2<i32>(f);
    let a = texel(i.x, i.y);
    let b = texel(i.x + 1, i.y);
    let c = texel(i.x, i.y + 1);
    let d = texel(i.x + 1, i.y + 1);
    let top = a * (256 - t.x) + b * t.x;
    let bot = c * (256 - t.x) + d * t.x;
    let n = top * (256 - t.y) + bot * t.y;               // <= 255 * 65536, exact in i32
    return (n + vec3<i32>(32768)) / vec3<i32>(65536);   // n >= 0: floor, i.e. round half up
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dimi = vec2<i32>(textureDimensions(srcTex, 0));
    let dim = vec2<f32>(dimi);
    let px = floor(in.uv * dim);                         // this texel
    let ip = clamp(vec2<i32>(px), vec2<i32>(0), dimi - vec2<i32>(1));
    let s = textureLoad(srcTex, ip, 0);                  // premultiplied
    let th = radians(P.direction);
    let v = max(P.relief, 0.0) * vec2<f32>(sin(th), -cos(th));
    let dd = vec3<f32>(bilin(px + v) - bilin(px - v));   // integer difference of the two 8-bit samples
    let base = round(straight8(s) * 255.0);
    let g = clamp(base + (max(P.contrast, 0.0) / 100.0) * dd, vec3<f32>(0.0), vec3<f32>(255.0)) / 255.0;
    let emb = vec4<f32>(g * s.a, s.a);
    return mix(emb, s, clamp(P.blend / 100.0, 0.0, 1.0));
}
