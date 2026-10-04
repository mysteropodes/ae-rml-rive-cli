// After Effects "Venetian Blinds" (ADBE Venetian Blinds) — UNVERIFIED: written from the effect's definition, not
// yet measured against After Effects renders. Stripes of Width pixels across Direction (AE angle, 0 = up, clockwise:
// t = p . (sin A, -cos A), layer px from the layer's top-left corner — the stripe origin is a guess to be measured);
// in every stripe the first Completion % of its width is wiped. Edges anti-aliased over one pixel, widened to Feather
// pixels. The alpha of the layer is multiplied by the matte, rounded to 8 bits.
struct Params {
    size: vec2<f32>,
    completion: f32,      // AE 1 Transition Completion (%)
    direction: f32,       // AE 2 Direction (degrees)
    width: f32,           // AE 3 Width (px)
    feather: f32,         // AE 4 Feather (px)
    passIndex: f32,
    pad0: f32,
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

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let ip = pixelOf(in.uv);
    let s = textureLoad(srcTex, ip, 0);
    let cw = clamp(P.completion, 0.0, 100.0) / 100.0;
    if (cw <= 0.0) {
        return s;
    }
    if (cw >= 1.0) {
        return vec4<f32>(0.0);
    }
    let w = max(P.width, 1.0);
    let a = radians(P.direction);
    let p = vec2<f32>(ip) + vec2<f32>(0.5) - P.layerRect.xy;
    let t = dot(p, vec2<f32>(sin(a), -cos(a)));
    let local = t - w * floor(t / w);          // 0..w inside the stripe
    let e = cw * w;                            // wiped: [0, e)
    var d: f32;                                // signed px, positive = kept
    if (local < e) {
        d = -min(local, e - local);
    } else {
        d = min(local - e, w - local);
    }
    let matte = clamp(d / max(P.feather, 1.0) + 0.5, 0.0, 1.0);
    return out8(straight8(s), s.a * matte);
}
