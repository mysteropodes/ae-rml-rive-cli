// After Effects "Displacement Map" (ADBE Displacement Map) — every output pixel (x, y) reads the layer at
// (x + dx, y + dy), bilinear on the PREMULTIPLIED pixels, transparent outside the layer (or wrapped around with Edge
// Behavior). dx = MaxH * (V - 128) / 128 where V is the 8-bit (0..255) value of the chosen map channel at the same pixel
// (measured: 128 = PF_HALF_CHAN8 is the neutral value, 255 gives +127/128 * Max; positive Max moves the image towards
// -x/-y, i.e. samples further right/down). Map channels are read from the straight (un-premultiplied) map colour;
// Luminance = round(0.299 R + 0.587 G + 0.114 B) (Rec. 601, measured on AE Shift Channels). Full / Half / Off are taken
// as the constant channel values 255 / 128 / 0 like Shift Channels' constants (NOT measured: Half = no displacement,
// Off = -Max). Hue / Lightness / Saturation = HLS of the straight map colour (NOT measured).
// Displacement Map Behavior uses textureDimensions(mapTex): Center Map (map centred, no displacement outside it),
// Stretch Map to Fit, Tile Map. In the generated Rive node the map is rendered at the layer size, so the three modes
// coincide there (as they do in AE for a map layer of the same size).
// Expand Output only grows the AE layer bounds beyond the layer; the canvas here is the layer, so it has no effect.
struct Params {
    size: vec2<f32>,
    hChan: f32,          // AE 2 Use For Horizontal Displacement popup 1..11 (1 Red 2 Green 3 Blue 4 Alpha 5 Luminance 6 Hue 7 Lightness 8 Saturation 9 Full 10 Half 11 Off)
    hMax: f32,           // AE 3 Max Horizontal Displacement px (-32000..32000)
    vChan: f32,          // AE 4 Use For Vertical Displacement popup 1..11 (same list)
    vMax: f32,           // AE 5 Max Vertical Displacement px (-32000..32000)
    behavior: f32,       // AE 6 Displacement Map Behavior popup 1..3 (1 Center Map 2 Stretch Map to Fit 3 Tile Map)
    wrap: f32,           // AE 7 Edge Behavior: Wrap Pixels Around checkbox 0/1
    expand: f32,         // AE 8 Expand Output checkbox 0/1 (no effect on a layer-sized canvas)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
};
@group(0) @binding(0) var srcTex: texture_2d<f32>;
@group(0) @binding(1) var srcSamp: sampler;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var mapTex: texture_2d<f32>;

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

// positive modulo for integer pixel indices
fn wrapi(i: i32, n: i32) -> i32 {
    let r = i % n;
    return select(r, r + n, r < 0);
}

// one layer pixel: transparent outside the layer, or wrapped around (Edge Behavior)
fn texel(ix: i32, iy: i32, dims: vec2<i32>) -> vec4<f32> {
    var x = ix;
    var y = iy;
    if (P.wrap > 0.5) {
        x = wrapi(x, dims.x);
        y = wrapi(y, dims.y);
    } else if (x < 0 || y < 0 || x >= dims.x || y >= dims.y) {
        return vec4<f32>(0.0);
    }
    return textureLoad(srcTex, vec2<i32>(x, y), 0);
}

// RGB (0..1) -> HLS hue, lightness, saturation (0..1)
fn hls(c: vec3<f32>) -> vec3<f32> {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let l = 0.5 * (mx + mn);
    let d = mx - mn;
    if (d <= 1e-6) {
        return vec3<f32>(0.0, l, 0.0);
    }
    let s = select(d / (2.0 - mx - mn), d / (mx + mn), l <= 0.5);
    var h: f32;
    if (mx == c.r) {
        h = (c.g - c.b) / d;
    } else if (mx == c.g) {
        h = 2.0 + (c.b - c.r) / d;
    } else {
        h = 4.0 + (c.r - c.g) / d;
    }
    h = h / 6.0;
    h = h - floor(h);
    return vec3<f32>(h, l, s);
}

// 8-bit value (0..255) of map channel `ch` (AE popup index); -1 = no displacement
fn chanValue(ch: i32, m: vec4<f32>, inside: bool) -> f32 {
    if (ch == 11) {
        return 0.0;                                          // Off = channel value 0
    }
    if (ch == 9) {
        return 255.0;                                        // Full
    }
    if (ch == 10) {
        return 128.0;                                        // Half (neutral)
    }
    if (!inside) {
        return 128.0;                                        // outside a centred map: no displacement
    }
    // 8-bit straight map pixel (AE 8 bpc)
    let a8 = floor(m.a * 255.0 + 0.5);
    var c8 = vec3<f32>(0.0);
    if (m.a > 0.0) {
        c8 = floor(clamp(m.rgb / m.a, vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + 0.5);
    }
    switch ch {
        case 1: { return c8.r; }
        case 2: { return c8.g; }
        case 3: { return c8.b; }
        case 4: { return a8; }
        case 5: {
            // Rec. 601 luma rounded, in integers (matches AE Shift Channels "Luminance" on 99.99 % of pixels)
            let ci = vec3<i32>(c8);
            return f32((299 * ci.r + 587 * ci.g + 114 * ci.b + 500) / 1000);
        }
        case 6: { return floor(hls(c8 / 255.0).x * 255.0 + 0.5); }
        case 7: { return floor(hls(c8 / 255.0).y * 255.0 + 0.5); }
        case 8: { return floor(hls(c8 / 255.0).z * 255.0 + 0.5); }
        default: { return 128.0; }
    }
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let p = floor(in.uv * P.size);                           // output pixel index (centre at +0.5)
    // map pixel for this output pixel (Displacement Map Behavior)
    let md = vec2<i32>(textureDimensions(mapTex));
    let mdf = vec2<f32>(md);
    var mp: vec2<f32>;
    var inside = true;
    let beh = i32(P.behavior + 0.5);
    if (beh == 2) {
        mp = floor((p + 0.5) * mdf / P.size);                // Stretch Map to Fit
    } else if (beh == 3) {
        mp = p - mdf * floor(p / mdf);                       // Tile Map
    } else {
        mp = p - floor(0.5 * (P.size - mdf));                // Center Map
        inside = all(mp >= vec2<f32>(0.0)) && all(mp < mdf);
    }
    let mi = clamp(vec2<i32>(mp), vec2<i32>(0), md - vec2<i32>(1));
    let m = textureLoad(mapTex, mi, 0);
    let vh = chanValue(i32(P.hChan + 0.5), m, inside);
    let vv = chanValue(i32(P.vChan + 0.5), m, inside);
    let d = vec2<f32>(P.hMax * (vh - 128.0), P.vMax * (vv - 128.0)) / 128.0;
    // bilinear on premultiplied pixels, pixel-index space (pixel i centre = i)
    let q = p + d;
    let q0 = floor(q);
    let f = q - q0;
    let ix = i32(q0.x);
    let iy = i32(q0.y);
    let c00 = texel(ix, iy, dims);
    let c10 = texel(ix + 1, iy, dims);
    let c01 = texel(ix, iy + 1, dims);
    let c11 = texel(ix + 1, iy + 1, dims);
    // keeps the sampler binding alive (auto bind-group layouts drop unused bindings); contributes nothing
    let keep = textureSampleLevel(srcTex, srcSamp, vec2<f32>(0.5), 0.0) * 0.0;
    return mix(mix(c00, c10, f.x), mix(c01, c11, f.x), f.y) + keep;
}
