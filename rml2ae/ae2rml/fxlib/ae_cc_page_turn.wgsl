// After Effects "CC Page Turn" (CC Page Turn) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A page curl: the fold line goes through Fold Position, perpendicular to Fold Direction (AE angle, pointing to the
// part that lifts). That part is turned over onto the page around a cylinder of Fold Radius: its back shows the
// layer mirrored, mixed with Paper Color by (100 - Back Opacity) %; Render 1 Front & Back Page, 2 Back Page, 3 Front
// Page. Light Direction shades the curl. Controls (corner presets) and a separate Back Page layer are not modelled.
struct Params {
    size: vec2<f32>,
    foldPos: vec2<f32>,   // AE 2 Fold Position (layer px)
    paper: vec4<f32>,     // AE 9 Paper Color
    foldDir: f32,         // AE 3 Fold Direction (degrees)
    radius: f32,          // AE 4 Fold Radius (px)
    light: f32,           // AE 5 Light Direction (degrees)
    render: f32,          // AE 6 Render (menu)
    backOpacity: f32,     // AE 8 Back Opacity (%)
    passIndex: f32,
    pad0: f32,
    pad1: f32,
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

fn inside(q: vec2<f32>) -> bool {
    let sz = layerSize();
    return q.x >= 0.0 && q.y >= 0.0 && q.x < sz.x && q.y < sz.y;
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let o = layerPt(P.foldPos);
    let dir = aeDir(P.foldDir);
    let d = dot(q - o, dir);                 // > 0: the lifted side
    let r = max(P.radius, 0.5);
    let mode = i32(round(P.render));
    // back of the page: the part beyond the fold, rolled over a cylinder of radius r onto d < 0
    // a point at -d (d < 0) on the flap came from distance pi r / 2 + ... ; flat beyond the cylinder: mirror at 2r
    var back = vec4<f32>(0.0);
    var backShade = 1.0;
    if (d < 0.0 && d > -2.0 * r - length(layerSize())) {
        var srcD: f32;
        if (-d <= r) {
            let a = asin(clamp(-d / r, -1.0, 1.0));        // on the cylinder's top half
            srcD = r * (3.14159265 - a);
            backShade = 0.6 + 0.4 * cos(a);
        } else {
            srcD = 3.14159265 * r + (-d - r) * 0.0 + (-d);   // flat part of the flap, mirrored
        }
        let src = q + dir * (srcD - d);
        if (inside(src) && dot(src - o, dir) > 0.0) {
            let t = tapL(src);
            let c = straight8(t);
            let pc = mix(P.paper.rgb, c, clamp(P.backOpacity / 100.0, 0.0, 1.0));
            let sh = backShade * (0.85 + 0.15 * dot(aeDir(P.light), dir));
            back = vec4<f32>(pc * sh * t.a, t.a);
        }
    }
    var front = vec4<f32>(0.0);
    if (d <= 0.0 && mode != 2) {
        front = tapL(q);
    }
    if (mode == 3) {
        return round(front * 255.0) / 255.0;
    }
    let outc = back + front * (1.0 - back.a);
    return round(outc * 255.0) / 255.0;
}
