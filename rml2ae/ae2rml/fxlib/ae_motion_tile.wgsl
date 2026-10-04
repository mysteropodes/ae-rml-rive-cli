// After Effects "Motion Tile" (ADBE Tile) — the layer is scaled into a tile (Tile Width/Height % of the layer) and
// repeated around Tile Center; Mirror Edges flips every other tile; Phase (360 deg = one tile) offsets every other
// column vertically, or every other row horizontally with Horizontal Phase Shift (measured: odd rows move right by
// Phase/360 * tile width, the row holding Tile Center stays put); Output Width/Height < 100 % crop around the layer
// centre (> 100 % would need the canvas grown by `pad`, which this shader cannot see: it assumes canvas = layer).
// Measured resampling (pixel-exact vs AE 2026, 8 bpc): AE builds the tile image by exact AREA AVERAGING (each tile
// pixel = mean of a (100/tile%)-px box of the layer, grid anchored at the tile corner) and places tiles that land on
// fractional positions (Phase shifts) with BILINEAR interpolation of that tile image; 8-bit results round x.5 up.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,       // AE 1 Tile Center (point, layer px)
    tileW: f32,              // AE 2 Tile Width 0..100 %
    tileH: f32,              // AE 3 Tile Height 0..100 %
    outW: f32,               // AE 4 Output Width 0..30000 %
    outH: f32,               // AE 5 Output Height 0..30000 %
    mirror: f32,             // AE 6 Mirror Edges checkbox 0/1
    phase: f32,              // AE 7 Phase degrees (360 = one tile)
    hShift: f32,             // AE 8 Horizontal Phase Shift checkbox 0/1
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

fn odd(i: f32) -> bool {
    return abs(i - 2.0 * floor(i * 0.5)) > 0.5;
}

const MAXPAIRS: i32 = 8;                                    // exact box up to 16 source px per axis (tile >= ~6.7 %)

// i-th merged tap of the box [a, b] on an axis of n texels (texel k covers [k, k+1]): (position, weight, 1), or
// (0, 0, 0) when there is none. Two adjacent texels are merged into one bilinear tap (pos = k + 0.5 + w1 / (w0 + w1)),
// texels outside the layer weigh 0 (transparent); footprints wider than 2*MAXPAIRS texels fall back to MAXPAIRS evenly
// spaced bilinear taps (approximate box). Computed per index, not stored in an array: Direct3D's FXC compiler cannot
// build a loop that writes into a local array at a variable index (X3511 "forced to unroll loop"). Same taps, same
// order, same arithmetic as the stored list it replaces.
fn tapAt(a: f32, b: f32, n: f32, i: i32) -> vec3<f32> {
    let k0 = floor(a);
    let k1 = ceil(b) - 1.0;
    if (k1 - k0 + 1.0 <= f32(2 * MAXPAIRS)) {
        let k = k0 + 2.0 * f32(i);
        if (k > k1) { return vec3<f32>(0.0); }
        var w0 = min(b, k + 1.0) - max(a, k);
        var w1 = select(0.0, min(b, k + 2.0) - max(a, k + 1.0), k + 1.0 <= k1);
        if (k < 0.0 || k >= n) { w0 = 0.0; }
        if (k + 1.0 < 0.0 || k + 1.0 >= n) { w1 = 0.0; }
        let w = w0 + w1;
        if (w > 0.0) { return vec3<f32>(k + 0.5 + w1 / w, w, 1.0); }
        return vec3<f32>(0.0);
    }
    let L = (b - a) / f32(MAXPAIRS);
    let c = a + (f32(i) + 0.5) * L;
    if (c >= 0.0 && c <= n) { return vec3<f32>(clamp(c, 0.5, n - 0.5), L, 1.0); }
    return vec3<f32>(0.0);
}

fn tapCount(a: f32, b: f32, n: f32) -> i32 {
    var c = 0;
    for (var i = 0; i < MAXPAIRS; i = i + 1) {
        if (tapAt(a, b, n, i).z > 0.0) { c = c + 1; }
    }
    return c;
}

fn boxAvg(X: vec2<f32>, r: vec2<f32>) -> vec4<f32> {
    let ax = X.x - r.x;
    let bx = X.x + r.x;
    let ay = X.y - r.y;
    let by = X.y + r.y;
    var acc = vec4<f32>(0.0);
    for (var j = 0; j < MAXPAIRS; j = j + 1) {
        let ty = tapAt(ay, by, P.size.y, j);
        if (ty.z == 0.0) { continue; }
        for (var i = 0; i < MAXPAIRS; i = i + 1) {
            let tx = tapAt(ax, bx, P.size.x, i);
            if (tx.z == 0.0) { continue; }
            let q = vec2<f32>(tx.x, ty.x) / P.size;
            acc = acc + tx.y * ty.y * textureSampleLevel(srcTex, srcSamp, q, 0.0);
        }
    }
    return acc / (4.0 * r.x * r.y);
}

// tile-space coordinate c (output px from the origin tile's corner, one axis) -> layer coordinate (source px)
fn toSrc(c: f32, t: f32, n: f32) -> f32 {
    let i = floor(c / t);
    var u = (c - i * t) / t;
    if (P.mirror > 0.5 && odd(i)) { u = 1.0 - u; }
    return u * n;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let p = in.uv * P.size;                                  // layer px (pixel centres at .5)
    // Output Width/Height: crop around the layer centre
    let half = 0.5 * P.size * vec2<f32>(P.outW, P.outH) / 100.0;
    let d = abs(p - 0.5 * P.size);
    if (d.x > half.x || d.y > half.y) {
        return vec4<f32>(0.0);
    }
    let t = max(P.size * vec2<f32>(P.tileW, P.tileH) / 100.0, vec2<f32>(1e-3));
    let r = 0.5 * P.size / t;                                // half box footprint (source px) of one tile pixel
    let l = p - P.center + 0.5 * t;                          // tile space: relative to the origin tile's corner
    let ph = P.phase / 360.0;
    let hs = P.hShift > 0.5;
    // AE renders the tile image T (each tile px = area average of a (100/tile%)-px box of the layer, on a grid
    // anchored at the tile corner), then places every tile with bilinear sub-pixel interpolation of T
    // (the Phase-shifted rows/columns land on fractional positions). B = unshifted axis, A = shifted axis.
    let lA = select(l.y, l.x, hs);
    let lB = select(l.x, l.y, hs);
    let tA = select(t.y, t.x, hs);
    let tB = select(t.x, t.y, hs);
    let nA = select(P.size.y, P.size.x, hs);
    let nB = select(P.size.x, P.size.y, hs);
    let vB = lB - 0.5;
    let gB0 = floor(vB);
    let fB = vB - gB0;
    var acc = vec4<f32>(0.0);
    for (var jb = 0; jb < 2; jb = jb + 1) {
        let gB = gB0 + f32(jb);
        let wB = select(1.0 - fB, fB, jb == 1);
        if (wB <= 0.0) { continue; }
        let cB = gB + 0.5;
        let iB = floor(cB / tB);                             // tile row (hShift) / column (vertical phase)
        let sA = select(0.0, ph * tA, odd(iB));
        let vA = lA - sA - 0.5;
        let gA0 = floor(vA);
        let fA = vA - gA0;
        let XB = toSrc(cB, tB, nB);
        for (var ja = 0; ja < 2; ja = ja + 1) {
            let gA = gA0 + f32(ja);
            let wA = select(1.0 - fA, fA, ja == 1);
            if (wA <= 0.0) { continue; }
            let XA = toSrc(gA + 0.5, tA, nA);
            let X = select(vec2<f32>(XB, XA), vec2<f32>(XA, XB), hs);
            acc = acc + wA * wB * boxAvg(X, r);
        }
    }
    // AE rounds x.5 up when quantising to 8 bpc; the GPU unorm conversion is round-to-nearest-even
    return min(acc + vec4<f32>(0.02 / 255.0) * step(vec4<f32>(1e-6), acc), vec4<f32>(1.0));
}
