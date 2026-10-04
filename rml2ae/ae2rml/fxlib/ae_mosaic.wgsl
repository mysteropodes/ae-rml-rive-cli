// After Effects "Mosaic" (ADBE Mosaic) — the layer is cut into Horizontal Blocks x Vertical Blocks tiles, each tile
// filled with the average of its pixels (PREMULTIPLIED average, rounded) or, with Sharp Colors, with one source pixel.
// Measured against AE 2026 8 bpc (640x360 layer, 20x10 and 40x20 tiles):
//   * tiles are anchored at the layer's top-left corner and span [i·W/n, (i+1)·W/n) EXACTLY, fractions included:
//     a tile's mean weights each pixel by the share of it inside the tile, and a pixel straddling two tiles is the
//     coverage-weighted mix of both means (area sampling; measured on the 13x7 held-out reference, 640x360);
//   * average = mean of the premultiplied RGBA of the tile, rounded to nearest (exact vs AE: mean err 0.015 %, max 1);
//   * Sharp Colors = the tile's LAST pixel (bottom-right: x = tile end − 1, y = tile end − 1), not its centre —
//     exact on all 800 tiles of the 40x20 reference.
// 4 separable passes (premultiplied RGBA8 intermediates):
//   pass 0 = horizontal chunk mean over [x, min(x + K, tile end)),  pass 1 = horizontal tile mean from the chunks,
//   pass 2 = vertical chunk mean,                                      pass 3 = vertical tile mean.
// K = 1 (plain copy, exact) while a tile is <= DIRECT_SPAN px long, else K = ceil(sqrt(tile length)), so a pass never
// needs more than ~max(DIRECT_SPAN, 2·sqrt(L)) taps (1 tile on a 30000 px layer: 174 taps).
// Sharp Colors: passes 0 and 2 copy, pass 1 picks the tile's last column, pass 3 its last row (bit-exact on
// whole-pixel tiles; non-divisible sizes with Sharp Colors are not measured). Edge pixels are copied by the chunk
// passes and weighted by their coverage in the gather passes.
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

// overlap, in 1/n px, of pixel q ([q, q+1)) with tile b ([b·L/n, (b+1)·L/n)), both scaled by n: [q·n, q·n+n) ∩ [b·L, b·L+L)
fn overlapN(q: i32, b: i32, n: i32, L: i32) -> i32 {
    return clamp(min((q + 1) * n, (b + 1) * L) - max(q * n, b * L), 0, n);
}

// mean of tile b from the chunk pass: edge pixels weighted by their coverage, interior chunks read at their starts
fn tileMean(px: vec2<i32>, b: i32, n: i32, L: i32, K: i32, horizontal: bool) -> vec4<f32> {
    let i0 = (b * L + n - 1) / n;                 // first interior pixel = ceil(b·L/n)
    let i1 = ((b + 1) * L) / n;                   // interior end (exclusive) = floor((b+1)·L/n)
    var acc = vec4<f32>(0.0);
    var q = i0;
    loop {
        if (q >= i1) { break; }
        let c = min(K, i1 - q);
        acc += textureLoad(srcTex, along(px, q, horizontal), 0) * f32(c * n);
        q += K;
    }
    if ((b * L) % n != 0) {                       // left edge pixel, partly in the previous tile
        let qe = (b * L) / n;
        acc += textureLoad(srcTex, along(px, qe, horizontal), 0) * f32(overlapN(qe, b, n, L));
    }
    if (((b + 1) * L) % n != 0 && i1 < L) {       // right edge pixel, partly in the next tile
        acc += textureLoad(srcTex, along(px, i1, horizontal), 0) * f32(overlapN(i1, b, n, L));
    }
    return acc / f32(L);
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
    if (n >= L) {                                 // tiles of at most one pixel: identity
        return textureLoad(srcTex, px, 0);
    }
    let b = (p * n) / L;                          // tile holding the left edge of this pixel
    if (P.sharp > 0.5) {
        let e = ((b + 1) * L + n - 1) / n;        // tile end (exclusive), integer pixels
        if (!gather) {
            return textureLoad(srcTex, px, 0);
        }
        return textureLoad(srcTex, along(px, e - 1, horizontal), 0);
    }
    let spanMax = (L + n - 1) / n + 1;
    var K = 1;
    if (spanMax > DIRECT_SPAN) {
        K = i32(ceil(sqrt(f32(spanMax))));
    }
    if (!gather) {
        // interior pixels: chunk mean [p, min(p + K, interior end)); edge pixels (shared by two tiles): copied
        let i1 = ((b + 1) * L) / n;
        if ((p + 1) * n > (b + 1) * L) {
            return textureLoad(srcTex, px, 0);
        }
        let stop = min(p + K, i1);
        var acc = vec4<f32>(0.0);
        var q = p;
        loop {
            if (q >= stop) { break; }
            acc += textureLoad(srcTex, along(px, q, horizontal), 0);
            q += 1;
        }
        return acc / f32(stop - p);
    }
    // a pixel on a fractional tile boundary mixes the two tiles by coverage (area sampling, measured on 13x7)
    let b1 = ((p + 1) * n - 1) / L;
    var o = tileMean(px, b, n, L, K, horizontal) * f32(overlapN(p, b, n, L));
    if (b1 != b) {
        o += tileMean(px, b1, n, L, K, horizontal) * f32(overlapN(p, b1, n, L));
    }
    return o / f32(n);
}
