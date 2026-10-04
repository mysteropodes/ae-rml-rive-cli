// After Effects "CC Cylinder" (CC Cylinder) — UNVERIFIED: written from the effect's definition, not yet measured against After Effects renders (fxref/spec.py, fxlib check <slug> --holdout).
// Wraps the layer around a vertical cylinder at Position: at Radius 100 % the layer covers the front half (arc
// length pi R = the layer's width), the back half is empty; turned by Rotation Y (Rotation X / Z are not modelled). Render 1 Full, 2 Outside
// (front), 3 Inside (the inner back). Lambert shading as CC Sphere (assumed parameter positions).
struct Params {
    size: vec2<f32>,
    position: vec2<f32>,  // AE 2 Position (layer px)
    radius: f32,          // AE 1 Radius (%)
    rx: f32,              // AE 3 Rotation X
    ry: f32,              // AE 4 Rotation Y
    rz: f32,              // AE 5 Rotation Z
    render: f32,          // AE 6 Render (menu)
    lightIntensity: f32,  // AE 8 Light Intensity
    lightHeight: f32,     // AE 10 Light Height
    lightDir: f32,        // AE 11 Light Direction
    ambient: f32,         // AE 13 Ambient
    diffuse: f32,         // AE 14 Diffuse
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

fn rotX(p: vec3<f32>, a: f32) -> vec3<f32> { return vec3<f32>(p.x, p.y * cos(a) - p.z * sin(a), p.y * sin(a) + p.z * cos(a)); }
fn rotY(p: vec3<f32>, a: f32) -> vec3<f32> { return vec3<f32>(p.x * cos(a) + p.z * sin(a), p.y, -p.x * sin(a) + p.z * cos(a)); }
fn rotZ(p: vec3<f32>, a: f32) -> vec3<f32> { return vec3<f32>(p.x * cos(a) - p.y * sin(a), p.x * sin(a) + p.y * cos(a), p.z); }

// Lambert light from Light Direction (AE angle in the image plane) at Light Height (-100..100 -> elevation)
fn shade(nrm: vec3<f32>) -> f32 {
    let d = aeDir(P.lightDir);
    let h = clamp(P.lightHeight / 100.0, -1.0, 1.0);
    let L = normalize(vec3<f32>(d.x * (1.0 - abs(h)), d.y * (1.0 - abs(h)), max(h, 0.0) + 0.3));
    let diff = max(dot(nrm, L), 0.0);
    return clamp(P.ambient / 100.0 + P.diffuse / 100.0 * diff * P.lightIntensity / 100.0, 0.0, 2.0);
}

@fragment
fn fs_main(in: VSOut) -> @location(0) vec4<f32> {
    let q = layerPos(in.uv);
    let sz = layerSize();
    let R = max(P.radius / 100.0 * sz.x / 3.14159265, 1.0);
    let c = layerPt(P.position);
    let x = (q.x - c.x) / R;
    if (abs(x) >= 1.0) {
        return vec4<f32>(0.0);
    }
    var z = sqrt(1.0 - x * x);
    if (i32(round(P.render)) == 3) {
        z = -z;
    }
    var ang = atan2(x, z) - radians(P.ry);
    ang = ang - 6.28318531 * floor((ang + 3.14159265) / 6.28318531);   // -pi..pi
    if (abs(ang) > 1.57079633) {
        return vec4<f32>(0.0);
    }
    let u = ang / 3.14159265 + 0.5;
    let v = q.y - c.y + 0.5 * sz.y;
    let t = tapL(vec2<f32>(u * sz.x, v));
    var nrm = vec3<f32>(x, 0.0, z);
    if (z < 0.0) {
        nrm = -nrm;
    }
    let k = shade(nrm);
    return round(vec4<f32>(clamp(t.rgb * k, vec3<f32>(0.0), vec3<f32>(t.a)), t.a) * 255.0) / 255.0;
}
