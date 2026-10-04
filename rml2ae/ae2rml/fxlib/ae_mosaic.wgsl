// After Effects "Mosaic" (ADBE Mosaic) — the layer is cut into Horizontal Blocks x Vertical Blocks tiles, each tile
// filled with the average of its pixels (PREMULTIPLIED average, rounded) or, with Sharp Colors, with one source pixel.
// Measured against AE 2026 8 bpc (640x360 layer, 20x10 and 40x20 tiles):
//   * tiles are anchored at the layer's top-left corner; pixel x belongs to tile floor(x·n/W) (integer maths), so
//     tile i spans [ceil(i·W/n), ceil((i+1)·W/n)) — identical to W/n-wide tiles when W is a multiple of n
//     (the rule for non-divisible sizes is the natural per-pixel one, not measurable on the visible refs);
//   * average = mean of the premultiplied RGBA of the tile, rounded to nearest (exact vs AE: mean err 0.015 %, max 1);
//   * Sharp Colors = the tile's LAST pixel (bottom-right: x = tile end − 1, y = tile end − 1), not its centre —
//     exact on all 800 tiles of the 40x20 reference.
// 4 separable passes (premultiplied RGBA8 intermediates):
//   pass 0 = horizontal chunk mean over [x, min(x + K, tile end)),  pass 1 = horizontal tile mean from the chunks,
//   pass 2 = vertical chunk mean,                                      pass 3 = vertical tile mean.
// K = 1 (plain copy, exact) while a tile is <= DIRECT_SPAN px long, else K = ceil(sqrt(tile length)), so a pass never
// needs more than ~max(DIRECT_SPAN, 2·sqrt(L)) taps (1 tile on a 30000 px layer: 174 taps).
// Sharp Colors: passes 0 and 2 copy, pass 1 picks the tile's last column, pass 3 its last row (bit-exact).
// The tile grid is computed on the canvas size: keep the node's `pad` at 0 (Mosaic never spills out of the layer).
struct Params {
    size: vec2<f32>,
    hBlocks: f32,         // AE 1 Horizontal Blocks 1..4000 (integer, default 10)
    vBlocks: f32,         // AE 2 Vertical Blocks 1..4000 (integer, default 10)
    sharp: f32,           // AE 3 Sharp Colors checkbox 0/1 (default 0)
    passIndex: f32,
    pad1: f32,
    pad2: f32,
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

const DIRECT_SPAN: i32 = 128;

fn blockCount(v: f32) -> i32 {                 // AE integer slider (keyframes interpolate then round)
    return clamp(i32(floor(v + 0.5)), 1, 30000);
}

fn along(px: vec2<i32>, q: i32, horizontal: bool) -> vec2<i32> {
    return select(vec2<i32>(px.x, q), vec2<i32>(q, px.y), horizontal);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let px = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0, 0), dims - vec2<i32>(1, 1));
    let pi = i32(P.passIndex + 0.5);
    let horizontal = pi < 2;
    let gather = (pi % 2) == 1;
    let L = select(dims.y, dims.x, horizontal);
    let n = select(blockCount(P.vBlocks), blockCount(P.hBlocks), horizontal);
    let p = select(px.y, px.x, horizontal);
    let b = (p * n) / L;                          // tile index of this pixel
    let s = (b * L + n - 1) / n;                  // tile start = ceil(b·L/n)
    let e = ((b + 1) * L + n - 1) / n;            // tile end (exclusive)
    if (P.sharp > 0.5) {
        if (!gather) {
            return textureLoad(srcTex, px, 0);
        }
        return textureLoad(srcTex, along(px, e - 1, horizontal), 0);
    }
    let spanMax = (L + n - 1) / n;
    var K = 1;
    if (spanMax > DIRECT_SPAN) {
        K = i32(ceil(sqrt(f32(spanMax))));
    }
    var acc = vec4<f32>(0.0);
    if (!gather) {
        // chunk mean [p, min(p + K, e)) — only the chunk starts s + c·K are read by the next pass
        let stop = min(p + K, e);
        var q = p;
        loop {
            if (q >= stop) { break; }
            acc += textureLoad(srcTex, along(px, q, horizontal), 0);
            q += 1;
        }
        return acc / f32(stop - p);
    }
    var q = s;
    loop {
        if (q >= e) { break; }
        let c = min(K, e - q);
        acc += textureLoad(srcTex, along(px, q, horizontal), 0) * f32(c);
        q += K;
    }
    return acc / f32(e - s);
}
