"""After Effects 3D: layer world matrices (expressions included), the active camera, projection to comp space.

AE conventions come from py-aep's transform resolver (verified there against AE 2026): local matrix
T(position) · orientation · Rx · Ry · Rz · S(scale) · T(-anchor); two-node camera = look-at toward its point of
interest (stored in the anchor slot), then orientation and rotations; comp point = centre + zoom · (x, y) / z in camera
space; no camera layer = the default comp camera (centred, zoom = width · pixel aspect / 0.72, at z = -zoom).

The converter draws a 3D layer with the 2D transform AE gives its 2D children (F, flattened) and corrects it with
the homography H' = H · F⁻¹, where H maps the layer's plane to the comp through the camera.
"""
import math

from py_aep.resolvers.transform import (Mat4, _look_at_rotation, _rotate_x, _rotate_y, _rotate_z,
                                        build_local_matrix, default_camera_zoom)

from .aexpr import tonum, unwrap


# depth of field: Gaussian σ (comp px) per pixel of thin-lens circle of confusion. Measured on After Effects 2026
# renders (Classic 3D, aerender, shape and solid layers 200 to 2,000 px off the focus plane, aperture ×1 to ×4):
# σ ≈ 0.036-0.05 × the circle of confusion — Classic 3D blurs far less than a real lens would (σ = c / 4)
DOF_K = 0.04


def is3d(L):
    return bool(getattr(L, "three_d_layer", False)) and type(L).__name__ not in ("CameraLayer", "LightLayer")


def _v(x, n):
    x = x if isinstance(x, list) else [x]
    return [tonum(x[i]) if i < len(x) else 0.0 for i in range(n)]


class Projector:
    """one per composition"""

    def __init__(self, engine, comp):
        self.engine, self.comp = engine, comp
        self.env = None               # an expression's Env while it asks toComp: time dependence is tracked there
        self.w, self.h = float(comp.width), float(comp.height)
        self.pa = float(getattr(comp, "pixel_aspect", 1.0) or 1.0)
        self.cams = [L for L in comp.layers if type(L).__name__ == "CameraLayer" and getattr(L, "enabled", True)]
        self.has_3d = bool(self.cams) or any(is3d(L) for L in comp.layers)

    # ---------------------------------------------------------------- transforms
    def value(self, L, mn, t, default):
        try:
            p = L.transform.property(mn)
        except Exception:
            p = None
        if p is None:
            return default
        return unwrap(self.engine.value(p, L, self.comp, t, parent_env=self.env))

    def position(self, L, t):
        tr = L.transform
        try:
            sep = tr.property("ADBE Position_0") is not None and bool(getattr(tr.property("ADBE Position"), "dimensions_separated", False))
        except Exception:
            sep = False
        if sep:
            return [tonum(self.value(L, f"ADBE Position_{i}", t, 0.0)) for i in range(3)]
        return _v(self.value(L, "ADBE Position", t, [0, 0, 0]), 3)

    def local(self, L, t, flatten=False):
        pos = self.position(L, t)
        anc = _v(self.value(L, "ADBE Anchor Point", t, [0, 0, 0]), 3)
        sc = _v(self.value(L, "ADBE Scale", t, [100, 100, 100]), 3)
        if len(sc) < 3 or not is3d(L):
            sc[2] = 100.0
        rz = tonum(self.value(L, "ADBE Rotate Z", t, 0.0))
        if not is3d(L) and type(L).__name__ != "CameraLayer":
            return build_local_matrix([pos[0], pos[1], 0.0], [anc[0], anc[1], 0.0], sc, rz)
        ori = _v(self.value(L, "ADBE Orientation", t, [0, 0, 0]), 3)
        rx = tonum(self.value(L, "ADBE Rotate X", t, 0.0))
        ry = tonum(self.value(L, "ADBE Rotate Y", t, 0.0))
        if flatten:
            return build_local_matrix([pos[0], pos[1], 0.0], anc, sc, rz + ori[2])
        return build_local_matrix(pos, anc, sc, rz, orientation=ori, rotate_x=rx, rotate_y=ry)

    def world(self, L, t):
        chain = []
        cur = L
        while cur is not None:
            chain.append(cur)
            cur = cur.parent
        m = Mat4.identity()
        for lay in reversed(chain):
            loc = self.local(lay, t)
            if lay is L and is3d(L) and _auto_orient(L) == 4214:
                # "Orient Towards Camera": the plane turns to face the camera (look-at from the camera, +Y up),
                # applied about the layer's position, before its own orientation / rotations
                # the camera-facing turn REPLACES the rotation the parents carry: only their position and scale
                # stay (broadcast-test's popsicle: parent X -78°, own orientation +78° and X -76° — AE shows it standing,
                # the parent's -78° applied first laid it flat)
                p = self.position(L, t)
                wp = m.transform_point(p)
                _R, C, _z = self.camera(t)
                look = _look_at_rotation(C, wp)
                win = Mat4.identity()
                win[0][3], win[1][3], win[2][3] = wp[0], wp[1], wp[2]
                pout = Mat4.identity()
                pout[0][3], pout[1][3], pout[2][3] = -p[0], -p[1], -p[2]
                ps = Mat4.identity()
                for k in range(3):
                    ps[k][k] = math.sqrt(sum(m[r][k] ** 2 for r in range(3))) or 1.0
                m = win @ look @ ps @ pout @ loc
                continue
            m = m @ loc
        return m

    # ---------------------------------------------------------------- camera
    def active_camera(self, t):
        for c in self.cams:
            if float(c.in_point) <= t < float(c.out_point):
                return c
        return None

    def camera(self, t):
        """-> (R rotation camera→world, C position, zoom)"""
        cam = self.active_camera(t)
        cx, cy = self.w / 2.0, self.h / 2.0
        if cam is None:
            zoom = default_camera_zoom(self.w, self.pa)
            return Mat4.identity(), [cx, cy, -zoom], zoom
        zoom = default_camera_zoom(self.w, self.pa)
        try:
            p = cam.property("ADBE Camera Options Group").property("ADBE Camera Zoom")
            zoom = tonum(unwrap(self.engine.value(p, cam, self.comp, t, parent_env=self.env)))
        except Exception:
            pass
        pos = self.position(cam, t)
        pp = cam.transform.property("ADBE Position")
        if pp is not None and not getattr(pp, "is_modified", True) and not self.engine.has_expr(pp):
            # an untouched camera position is AE's dynamic default (centre, -zoom); the stored bytes are not it
            # (TR_Fold: stored [960, 540, 0] — the flat first frame of the fold needs the camera at z = -zoom)
            pos = [cx, cy, -zoom]
        ori = _v(self.value(cam, "ADBE Orientation", t, [0, 0, 0]), 3)
        rx = tonum(self.value(cam, "ADBE Rotate X", t, 0.0))
        ry = tonum(self.value(cam, "ADBE Rotate Y", t, 0.0))
        rz = tonum(self.value(cam, "ADBE Rotate Z", t, 0.0))
        rot = Mat4.identity()
        try:
            two_node = int(getattr(cam.auto_orient, "value", cam.auto_orient)) == 4214    # CAMERA_OR_POINT_OF_INTEREST
        except Exception:
            two_node = False
        if two_node:
            poi = _v(self.value(cam, "ADBE Anchor Point", t, [cx, cy, 0]), 3)
            rot = _look_at_rotation(pos, poi)
        rot = rot @ _rotate_x(ori[0]) @ _rotate_y(ori[1]) @ _rotate_z(ori[2]) @ _rotate_x(rx) @ _rotate_y(ry) @ _rotate_z(rz)
        if cam.parent is not None:
            pw = self.world(cam.parent, t)
            pos = pw.transform_point(pos)
            rot = _rotation_only(pw) @ rot
        return rot, pos, zoom

    def camera_option(self, cam, mn, t, default):
        try:
            p = cam.property("ADBE Camera Options Group").property(mn)
        except Exception:
            p = None
        if p is None:
            return default
        try:
            return tonum(unwrap(self.engine.value(p, cam, self.comp, t, parent_env=self.env)))
        except Exception:
            return default

    def dof(self, t):
        """the active camera's depth of field at t: (focus distance, aperture, blur level / 100, zoom), or None"""
        cam = self.active_camera(t)
        if cam is None or self.camera_option(cam, "ADBE Camera Depth of Field", t, 0.0) < 0.5:
            return None
        zoom = self.camera(t)[2]
        return (self.camera_option(cam, "ADBE Camera Focus Distance", t, zoom),
                self.camera_option(cam, "ADBE Camera Aperture", t, 0.0),
                self.camera_option(cam, "ADBE Camera Blur Level", t, 100.0) / 100.0, zoom)

    def dof_sigma(self, L, t, p=(0.0, 0.0)):
        """depth-of-field blur at the layer point p, as a Gaussian σ in comp pixels: the thin lens' circle of
        confusion seen through the camera — aperture × |depth − focus| / depth, scaled from the focus plane to the
        image by zoom / focus, times the blur level. 0 without depth of field"""
        d = self.dof(t)
        if d is None:
            return 0.0
        focus, aperture, level, zoom = d
        z = self.depth(L, t, p)
        if z <= 1e-6 or focus <= 1e-6:
            return 0.0
        return level * aperture * abs(z - focus) / z * zoom / focus * DOF_K

    def to_camera(self, X, cam):
        R, C, _zoom = cam
        d = [X[0] - C[0], X[1] - C[1], X[2] - C[2]]
        # R is orthonormal: camera coordinates = Rᵀ · d
        return [sum(R[r][c] * d[r] for r in range(3)) for c in range(3)]

    def project(self, X, t, cam=None):
        cam = cam or self.camera(t)
        q = self.to_camera(X, cam)
        if q[2] <= 1e-6:
            return None
        z = cam[2]
        return [self.w / 2.0 + z * q[0] / q[2], self.h / 2.0 + z * q[1] / q[2]]

    def to_comp(self, L, p, t):
        X = self.world(L, t).transform_point(_v(p, 3))
        r = self.project(X, t)
        return r if r is not None else [X[0], X[1]]

    def from_comp(self, L, q, t):
        """comp point -> layer point: the camera ray meets the layer plane (None when it does not, going forward)"""
        R, C, zoom = self.camera(t)
        dcam = [q[0] - self.w / 2.0, q[1] - self.h / 2.0, zoom]
        d = [sum(R[r][c] * dcam[c] for c in range(3)) for r in range(3)]
        try:
            inv = self.world(L, t).inverse()
        except ValueError:
            return None
        o_l = inv.transform_point(C)
        d_l = inv.transform_vector(d)
        if abs(d_l[2]) < 1e-12:
            return None
        k = -o_l[2] / d_l[2]
        if k <= 0:
            return None
        return [o_l[0] + k * d_l[0], o_l[1] + k * d_l[1]]

    def depth(self, L, t, p=(0.0, 0.0)):
        X = self.world(L, t).transform_point([p[0], p[1], 0.0])
        return self.to_camera(X, self.camera(t))[2]

    # ---------------------------------------------------------------- homography of the layer plane
    def homography(self, L, t):
        """3×3 H: layer point (x, y) → comp point, through the camera. None when the plane is edge-on."""
        W = self.world(L, t)
        R, C, zoom = cam = self.camera(t)
        cx, cy = self.w / 2.0, self.h / 2.0
        a = self.to_camera([W[0][0] + C[0], W[1][0] + C[1], W[2][0] + C[2]], cam)      # Rᵀ · column 0
        b = self.to_camera([W[0][1] + C[0], W[1][1] + C[1], W[2][1] + C[2]], cam)      # Rᵀ · column 1
        d = self.to_camera([W[0][3], W[1][3], W[2][3]], cam)                           # Rᵀ · (origin - C)
        H = [[zoom * a[0] + cx * a[2], zoom * b[0] + cx * b[2], zoom * d[0] + cx * d[2]],
             [zoom * a[1] + cy * a[2], zoom * b[1] + cy * b[2], zoom * d[1] + cy * d[2]],
             [a[2], b[2], d[2]]]
        return H


def _auto_orient(L):
    try:
        return int(getattr(L.auto_orient, "value", L.auto_orient))
    except Exception:
        return 0


def _rotation_only(m):
    """the rotation part of an affine Mat4 (scale removed per column)"""
    r = Mat4.identity()
    for c in range(3):
        n = math.sqrt(sum(m[k][c] ** 2 for k in range(3))) or 1.0
        for k in range(3):
            r[k][c] = m[k][c] / n
    return r


# ---------------------------------------------------------------- 3×3 helpers
def h_mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def h_apply(H, x, y):
    w = H[2][0] * x + H[2][1] * y + H[2][2]
    if abs(w) < 1e-12:
        return None
    return ((H[0][0] * x + H[0][1] * y + H[0][2]) / w, (H[1][0] * x + H[1][1] * y + H[1][2]) / w)


def affine3(m):
    """geom 2D affine (a, b, c, d, e, f) -> 3×3"""
    a, b, c, d, e, f = m
    return [[a, c, e], [b, d, f], [0.0, 0.0, 1.0]]


def jacobian(H, x, y):
    """the affine that best matches H around (x, y): (a, b, c, d, e, f) in geom's convention"""
    w = H[2][0] * x + H[2][1] * y + H[2][2]
    u = H[0][0] * x + H[0][1] * y + H[0][2]
    v = H[1][0] * x + H[1][1] * y + H[1][2]
    dux = (H[0][0] * w - u * H[2][0]) / (w * w)
    duy = (H[0][1] * w - u * H[2][1]) / (w * w)
    dvx = (H[1][0] * w - v * H[2][0]) / (w * w)
    dvy = (H[1][1] * w - v * H[2][1]) / (w * w)
    px, py = u / w, v / w
    return (dux, dvx, duy, dvy, px - dux * x - duy * y, py - dvx * x - dvy * y)


def h_inv(H):
    a, b, c = H[0]
    d, e, f = H[1]
    g, h, i = H[2]
    A, B, C = e * i - f * h, -(d * i - f * g), d * h - e * g
    det = a * A + b * B + c * C
    if abs(det) < 1e-18:
        return None
    return [[A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
            [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
            [C / det, -(a * h - b * g) / det, (a * e - b * d) / det]]


def quad_homography(src, dst):
    """the homography sending 4 points src to 4 points dst (Gaussian elimination, h33 = 1); None if degenerate"""
    A, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    n = 8
    M = [A[r] + [rhs[r]] for r in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        for r in range(n):
            if r != col:
                k = M[r][col] / M[col][col]
                for j in range(col, n + 1):
                    M[r][j] -= k * M[col][j]
    h = [M[r][n] / M[r][r] for r in range(n)]
    return [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]]


def svd_nodes(a, b, c, d):
    """2×2 linear part (x' = a x + c y, y' = b x + d y) = R(t1) · S(sx, sy) · R(t2) (Blinn's closed form)"""
    E, F = (a + d) / 2.0, (a - d) / 2.0
    G, H = (b + c) / 2.0, (b - c) / 2.0
    Q, R = math.hypot(E, H), math.hypot(F, G)
    sx, sy = Q + R, Q - R
    a1, a2 = math.atan2(G, F), math.atan2(H, E)
    return (a2 + a1) / 2.0, sx, sy, (a2 - a1) / 2.0


def h_path(p, H):
    """a geom path (vertices + RELATIVE tangents) through a homography: vertices and absolute handles mapped"""
    v, i, o, c = p
    nv, ni, no = [], [], []
    for (x, y), (ix, iy), (ox, oy) in zip(v, i, o):
        q = h_apply(H, x, y) or (x, y)
        a = h_apply(H, x + ix, y + iy) or q
        b = h_apply(H, x + ox, y + oy) or q
        nv.append(q)
        ni.append((a[0] - q[0], a[1] - q[1]))
        no.append((b[0] - q[0], b[1] - q[1]))
    return (nv, ni, no, c)


# ---------------------------------------------------------------- Classic 3D lights
# AE light types (LightLayer.light_type)
PARALLEL, SPOT, POINT, AMBIENT = 4412, 4413, 4414, 4415


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else [0.0, 0.0, 0.0]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


class Lighting:
    """After Effects' Classic 3D shading of one flat layer point, as a colour transform rgb -> rgb · A + B:
    Σ ambient lights × Ambient + Σ other lights × Diffuse × cos(normal, light) (Lambert), plus a Blinn-Phong
    highlight × Specular Intensity whose colour goes from the light's (Metal 0 %) to the layer's (Metal 100 %); spot
    cones, their feather and the falloff modes attenuate both. Every term was measured against After Effects 2026
    renders (24 cases, all within one 8-bit level). Shadows are not computed."""

    def __init__(self, pr):
        self.pr = pr
        self.lights = [L for L in pr.comp.layers if type(L).__name__ == "LightLayer" and getattr(L, "enabled", True)]

    def active(self, t):
        return [L for L in self.lights if float(L.in_point) <= t < float(L.out_point)]

    def _opt(self, L, group, mn, t, default):
        try:
            p = L.property(group).property(mn)
        except Exception:
            p = None
        if p is None:
            return default
        try:
            return unwrap(self.pr.engine.value(p, L, self.pr.comp, t, parent_env=self.pr.env))
        except Exception:
            return default

    def material(self, L, t):
        g = "ADBE Material Options Group"
        f = lambda mn, d: tonum(self._opt(L, g, mn, t, d))
        return {"accepts": f("ADBE Accepts Lights", 1.0) >= 0.5, "ambient": f("ADBE Ambient Coefficient", 100.0) / 100,
                "diffuse": f("ADBE Diffuse Coefficient", 50.0) / 100, "specular": f("ADBE Specular Coefficient", 50.0) / 100,
                "shininess": f("ADBE Shininess Coefficient", 5.0) / 100, "metal": f("ADBE Metal Coefficient", 100.0) / 100}

    def light(self, Lt, t):
        pr = self.pr
        g = "ADBE Light Options Group"
        o = lambda mn, d: self._opt(Lt, g, mn, t, d)
        col = o("ADBE Light Color", [1.0, 1.0, 1.0])
        col = [tonum(x) for x in (col if isinstance(col, list) else [1, 1, 1])[:3]]
        try:
            kind = int(getattr(Lt.light_type, "value", Lt.light_type))
        except Exception:
            kind = SPOT
        pos = pr.position(Lt, t)
        poi = _v(pr.value(Lt, "ADBE Anchor Point", t, [0, 0, 0]), 3)
        ori = _v(pr.value(Lt, "ADBE Orientation", t, [0, 0, 0]), 3)
        rx, ry, rz = (tonum(pr.value(Lt, f"ADBE Rotate {a}", t, 0.0)) for a in ("X", "Y", "Z"))
        rot = Mat4.identity()
        if _auto_orient(Lt) == 4214:
            rot = _look_at_rotation(pos, poi)
        rot = rot @ _rotate_x(ori[0]) @ _rotate_y(ori[1]) @ _rotate_z(ori[2]) @ _rotate_x(rx) @ _rotate_y(ry) @ _rotate_z(rz)
        if Lt.parent is not None:
            pw = pr.world(Lt.parent, t)
            pos = pw.transform_point(pos)
            rot = _rotation_only(pw) @ rot
        return {"kind": kind, "intensity": tonum(o("ADBE Light Intensity", 100.0)) / 100.0, "color": col,
                "pos": pos, "dir": _norm(rot.transform_vector([0.0, 0.0, 1.0])),
                "cone": tonum(o("ADBE Light Cone Angle", 90.0)), "feather": tonum(o("ADBE Light Cone Feather 2", 50.0)) / 100,
                "falloff": int(tonum(o("ADBE Light Falloff Type", 1.0))), "radius": tonum(o("ADBE Light Falloff Start", 500.0)),
                "distance": tonum(o("ADBE Light Falloff Distance", 500.0))}

    def shade(self, L, t, p=(0.0, 0.0)):
        """(A, B) per channel at the layer point p, or None when AE draws the layer unlit (no light on, or the layer
        does not accept lights)"""
        lights = self.active(t)
        if not lights:
            return None
        m = self.material(L, t)
        if not m["accepts"]:
            return None
        pr = self.pr
        W = pr.world(L, t)
        P = W.transform_point([p[0], p[1], 0.0])
        N = _norm([W[0][2], W[1][2], W[2][2]])
        _R, C, _z = pr.camera(t)
        V = _norm([C[i] - P[i] for i in range(3)])
        if _dot(N, V) < 0:
            N = [-x for x in N]                     # both faces take light: the one the camera sees
        A, S = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        for Lt in lights:
            li = self.light(Lt, t)
            k = li["intensity"]
            if li["kind"] == AMBIENT:
                for c in range(3):
                    A[c] += k * li["color"][c] * m["ambient"]
                continue
            if li["kind"] == PARALLEL:
                Lv = [-x for x in li["dir"]]
                att = 1.0
            else:
                d = [li["pos"][i] - P[i] for i in range(3)]
                dist = math.sqrt(_dot(d, d)) or 1e-9
                Lv = [x / dist for x in d]
                att = falloff(li["falloff"], dist, li["radius"], li["distance"])
                if li["kind"] == SPOT:
                    att *= spot_cone(math.degrees(math.acos(max(-1.0, min(1.0, -_dot(Lv, li["dir"]))))),
                                     li["cone"], li["feather"])
            if att <= 0:
                continue
            ndl = _dot(N, Lv)
            if ndl <= 0:
                continue
            dif = k * att * m["diffuse"] * ndl
            Hv = _norm([Lv[i] + V[i] for i in range(3)])
            spec = k * att * m["specular"] * max(0.0, _dot(N, Hv)) ** shininess_exponent(m["shininess"])
            for c in range(3):
                A[c] += dif * li["color"][c]
                S[c] += spec * li["color"][c]
        mt = m["metal"]
        return [A[c] + S[c] * mt for c in range(3)], [S[c] * (1 - mt) for c in range(3)]


def shininess_exponent(s):
    """Blinn-Phong exponent of a Specular Shininess fraction: the percentage itself (measured: 5 % and 50 % fit
    exponents 5 and 50 across the layer)"""
    return max(1.0, s * 100.0)


def falloff(kind, dist, radius, distance):
    """light falloff: 1 None, 2 Smooth (full up to the radius, then down to 0 over the falloff distance),
    3 Inverse Square Clamped (full inside the radius, then (radius / distance)²)"""
    if kind == 2:
        return 1.0 - _smooth((dist - radius) / max(distance, 1e-9)) if dist > radius else 1.0
    if kind == 3:
        return min(1.0, (radius / dist) ** 2) if dist > 1e-9 else 1.0
    return 1.0


def spot_cone(angle, cone, feather):
    """spot light: full inside the cone's half angle shrunk by the feather, 0 outside it, a smoothstep of the
    cosine in between (measured: AE 2026, cone 60°, feather 50 %, within 0.005)"""
    half = math.radians(cone / 2.0)
    inner = half * (1.0 - feather)
    a = math.radians(angle)
    if a <= inner:
        return 1.0
    if a >= half:
        return 0.0
    return _smooth((math.cos(a) - math.cos(half)) / max(math.cos(inner) - math.cos(half), 1e-12))
