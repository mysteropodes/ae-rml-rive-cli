// After Effects "Lens Flare" (ADBE Lens Flare) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// A flare is screened over the colour: a hot core and glow at Flare Center, a halo ring, and coloured ghost discs
// along the line from the centre through the layer centre; Lens Type (1 50-300mm Zoom, 2 35mm Prime, 3 105mm Prime)
// changes the sizes and the ghosts. Flare Brightness % scales it; Blend With Original % fades it. A stylised flare:
// AE's element shapes and colours are not reproduced.
struct Params {
    size: vec2<f32>,
    center: vec2<f32>,    // AE 1 Flare Center (layer px)
    brightness: f32,      // AE 2 Flare Brightness (%)
    lens: f32,            // AE 3 Lens Type (menu)
    blend: f32,           // AE 4 Blend With Original (%)
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

fn disc(p: vec2<f32>, c: vec2<f32>, r: f32, soft: f32) -> f32 {
    return 1.0 - smoothstep(r - soft, r + soft, length(p - c));
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let s = textureLoad(srcTex, pixelOf(in.uv), 0);
    let p = layerPos(in.uv);
    let sz = layerSize();
    let diag = length(sz);
    let c = layerPt(P.center);
    let mid = sz * 0.5;
    let lens = i32(round(P.lens));
    var k = 1.0;
    if (lens == 2) {
        k = 0.7;
    } else if (lens == 3) {
        k = 1.3;
    }
    let b = P.brightness / 100.0;
    let d = length(p - c) / diag;
    var f = vec3<f32>(1.0, 0.95, 0.85) * exp(-d * 40.0 / k) + vec3<f32>(1.0, 0.8, 0.6) * 0.35 * exp(-d * 6.0 / k);
    let ring = 0.12 * k * diag;
    f += vec3<f32>(0.6, 0.7, 1.0) * 0.15 * exp(-pow((length(p - c) - ring) / (0.01 * diag), 2.0));
    let axis = mid - c;
    let ghosts = array<vec4<f32>, 4>(vec4<f32>(0.6, 0.02, 0.4, 0.9), vec4<f32>(1.2, 0.035, 0.9, 0.5),
                                     vec4<f32>(1.6, 0.015, 0.3, 1.0), vec4<f32>(2.1, 0.05, 0.5, 0.6));
    for (var i = 0; i < 4; i++) {
        let g = ghosts[i];
        let gc = c + axis * g.x;
        let col = vec3<f32>(g.z, 0.6, g.w);
        f += col * 0.12 * disc(p, gc, g.y * k * diag, 2.0);
    }
    f = clamp(f * b, vec3<f32>(0.0), vec3<f32>(1.0));
    let o = straight8(s);
    let sc = vec3<f32>(1.0) - (vec3<f32>(1.0) - o) * (vec3<f32>(1.0) - f);
    return out8(mix(sc, o, clamp(P.blend / 100.0, 0.0, 1.0)), s.a);
}
