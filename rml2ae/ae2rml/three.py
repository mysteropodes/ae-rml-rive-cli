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
