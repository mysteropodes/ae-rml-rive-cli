// After Effects "Minimax" (ADBE Minimax, FR "Minimax") — measured pixel-exact on AE 2026 8 bpc (Minimum r 4,
// Maximum r 4, Maximum r 6 on Alpha and Color):
//   * the window is a SQUARE of side 2 r + 1 (not a disk / diamond), i.e. separable: horizontal then vertical;
//   * it runs on STRAIGHT (un-premultiplied) colour, channel by channel, the result is then premultiplied by the new
//     alpha (minimum / maximum of premultiplied values is clearly wrong: 1.3 % mean error);
//   * Channel 1 = Color (R, G, B; alpha untouched), 2 = Alpha and Color (all four). 3 = Alpha, 4 = Red, 5 = Green,
//     6 = Blue follow AE's menu order but are not covered by the refs (assumption);
//   * outside the layer = transparent black (straight 0); Don't Shrink Edges = 1 ignores the outside instead (so a
//     Minimum does not eat the content from the layer border — not covered by the refs, the content is inset).
// Operation 1 = Minimum, 2 = Maximum, 3 = Minimum then Maximum, 4 = Maximum then Minimum (the second stage runs on
// the first one's result, both with the same radius). Direction 1 = Horizontal and Vertical, 2 = Just Horizontal,
// 3 = Just Vertical. Radius rounded to an integer (fractional radii unmeasured).
// 4 passes: stage 1 horizontal, stage 1 vertical, stage 2 horizontal, stage 2 vertical (unused stages copy through).
// Intermediates hold STRAIGHT colour + alpha in RGBA8 (exact: AE works on straight 8-bit values); the last pass
// premultiplies. Cost 2 r + 1 taps per pass; radius capped at MAX_R (larger radii are clamped: approximation).
struct Params {
    size: vec2<f32>,
    operation: f32,       // AE 1 Operation popup 1..4 (default 2 = Maximum)
    radius: f32,          // AE 2 Radius px 0..32000 (default 0)
    channel: f32,         // AE 3 Channel popup 1..6 (default 1 = Color)
    direction: f32,       // AE 4 Direction popup 1..3 (default 1 = Horizontal and Vertical)
    dontShrink: f32,      // AE 5 Don't Shrink Edges checkbox 0/1
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

const MAX_R: i32 = 1024;

// straight colour + alpha of texel q (pass 0 reads the premultiplied content, later passes the straight intermediate)
fn straightAt(q: vec2<i32>, first: bool) -> vec4<f32> {
    let t = textureLoad(srcTex, q, 0);
    if (!first) {
        return t;
    }
    if (t.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    // un-premultiply on the 8-bit grid (AE holds straight 8-bit values)
    return vec4<f32>(clamp(round(t.rgb / t.a * 255.0) / 255.0, vec3<f32>(0.0), vec3<f32>(1.0)), t.a);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(srcTex));
    let p = clamp(vec2<i32>(floor(in.uv * vec2<f32>(dims))), vec2<i32>(0), dims - vec2<i32>(1));
    let pi = i32(round(P.passIndex));
    let first = pi == 0;
    let last = pi == 3;
    let op = i32(round(P.operation));
    let stage = pi / 2;                                 // 0 = first operation, 1 = second
    let horiz = (pi % 2) == 0;
    let dirMode = i32(round(P.direction));
    let r = min(i32(round(max(P.radius, 0.0))), MAX_R);
    // which operation this stage applies: 0 = none, 1 = minimum, 2 = maximum
    var kind = 0;
    if (stage == 0) {
        kind = select(select(op, 1, op == 3), 2, op == 4);
    } else if (op == 3) {
        kind = 2;
    } else if (op == 4) {
        kind = 1;
    }
    let axisOn = select(dirMode != 2, dirMode != 3, horiz);   // 2 = just horizontal, 3 = just vertical
    let c = straightAt(p, first);
    var res = c;
    if (kind != 0 && axisOn && r > 0) {
        let isMax = kind == 2;
        let dontShrink = P.dontShrink > 0.5;
        let dir = select(vec2<i32>(0, 1), vec2<i32>(1, 0), horiz);
        var acc = c;
        for (var i = -r; i <= r; i++) {
            let q = p + dir * i;
            var v = vec4<f32>(0.0);
            if (q.x < 0 || q.y < 0 || q.x >= dims.x || q.y >= dims.y) {
                if (dontShrink) {
                    continue;
                }
            } else {
                v = straightAt(q, first);
            }
            acc = select(min(acc, v), max(acc, v), isMax);
        }
        let ch = i32(round(P.channel));
        // channel mask: 1 Color, 2 Alpha and Color, 3 Alpha, 4 Red, 5 Green, 6 Blue
        var m = vec4<bool>(true, true, true, false);
        if (ch == 2) {
            m = vec4<bool>(true, true, true, true);
        } else if (ch == 3) {
            m = vec4<bool>(false, false, false, true);
        } else if (ch == 4) {
            m = vec4<bool>(true, false, false, false);
        } else if (ch == 5) {
            m = vec4<bool>(false, true, false, false);
        } else if (ch == 6) {
            m = vec4<bool>(false, false, true, false);
        }
        res = select(c, acc, m);
    }
    if (last) {
        return vec4<f32>(res.rgb * res.a, res.a);
    }
    return res;
}
