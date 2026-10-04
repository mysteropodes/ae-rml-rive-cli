"""Geometry: 2D affine matrices (AE transform stacks) and AE shape primitives as bezier paths.

A path is (verts, ins, outs, closed): tangents relative to their vertex, the After Effects convention.
The rectangle / ellipse / star constructions follow AE's own (same vertex order and start point), so a Trim Paths
measured on the converted path starts where AE starts it.
"""
import math

KAPPA = 0.5519150244935105707435627          # circle quarter as a cubic


# ------------------------------------------------------------------ affine  (a, b, c, d, tx, ty): x' = a x + c y + tx
IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m, n):
    """m ∘ n (n applied first)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def translate(x, y):
    return (1.0, 0.0, 0.0, 1.0, float(x), float(y))


def rotate_deg(r):
    t = math.radians(r)
    c, s = math.cos(t), math.sin(t)
    return (c, s, -s, c, 0.0, 0.0)


def scale(sx, sy):
    return (float(sx), 0.0, 0.0, float(sy), 0.0, 0.0)


def skew(sk_deg, axis_deg):
    """AE skew: shear by `sk` along the direction `axis` (0 = horizontal shear). The axis turns the other way from
    rotation (measured in AE 26.5: skew 30, axis 20 on a rotated rectangle, IoU 0.994 against 0.53 the other way)."""
    if not sk_deg:
        return IDENT
    r = rotate_deg(-axis_deg)
    ri = rotate_deg(axis_deg)
    sh = (1.0, 0.0, -math.tan(math.radians(sk_deg)), 1.0, 0.0, 0.0)
    return mul(r, mul(sh, ri))


def trs(pos, rot_deg, sc, anchor, sk=0.0, sk_axis=0.0):
    """AE transform: T(pos) R(rot) Skew S(scale) T(-anchor); scale in fractions (1 = 100 %)."""
    m = translate(-anchor[0], -anchor[1])
    m = mul(scale(sc[0], sc[1]), m)
    if sk:
        m = mul(skew(sk, sk_axis), m)
    m = mul(rotate_deg(rot_deg), m)
    return mul(translate(pos[0], pos[1]), m)


def apply(m, p):
    a, b, c, d, e, f = m
    x, y = p[0], p[1]
    return (a * x + c * y + e, b * x + d * y + f)


def apply_vec(m, v):
    a, b, c, d, _, _ = m
    return (a * v[0] + c * v[1], b * v[0] + d * v[1])


def invert(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * e + ic * f), -(ib * e + id_ * f))


def decompose(m):
    """-> (tx, ty, rotation rad, scaleX, scaleY, skew rad) with m = T R Skew S (Rive nodes have no skew)."""
    a, b, c, d, e, f = m
    sx = math.hypot(a, b)
    rot = math.atan2(b, a)
    det = a * d - b * c
    sy = det / sx if sx else math.hypot(c, d)
    # skew: angle between the transformed y axis and the perpendicular of the x axis
    shear = (a * c + b * d) / (sx * sx) if sx else 0.0
    return e, f, rot, sx, sy, math.atan(shear)


def is_identity(m, eps=1e-9):
    return all(abs(x - y) < eps for x, y in zip(m, IDENT))


# ------------------------------------------------------------------ paths
def path(verts, ins=None, outs=None, closed=True):
    n = len(verts)
    return ([tuple(v[:2]) for v in verts], [tuple(v[:2]) for v in (ins or [(0, 0)] * n)],
            [tuple(v[:2]) for v in (outs or [(0, 0)] * n)], bool(closed))


def reverse(p):
    v, i, o, c = p
    if not v:
        return p
    # keep the first vertex first (AE reverses around the start point)
    order = [0] + list(range(len(v) - 1, 0, -1))
    return ([v[k] for k in order], [o[k] for k in order], [i[k] for k in order], c)


def transform_path(p, m):
    v, i, o, c = p
    return ([apply(m, x) for x in v], [apply_vec(m, x) for x in i], [apply_vec(m, x) for x in o], c)


def rect_path(pos, size, roundness, direction=1):
    """AE rectangle: starts on the right edge (top end), clockwise; roundness clamped to half the short side."""
    px, py = pos[0], pos[1]
    hw, hh = abs(size[0]) / 2, abs(size[1]) / 2
    r = max(0.0, min(hw, hh, roundness))
    k = r * KAPPA
    L, R, T, B = px - hw, px + hw, py - hh, py + hh
    if r <= 1e-9:
        verts = [(R, T), (R, B), (L, B), (L, T)]
        p = path(verts, closed=True)
    else:
        verts = [(R, T + r), (R, B - r), (R - r, B), (L + r, B), (L, B - r), (L, T + r), (L + r, T), (R - r, T)]
        ins = [(0, -k), (0, 0), (k, 0), (0, 0), (0, k), (0, 0), (-k, 0), (0, 0)]
        outs = [(0, 0), (0, k), (0, 0), (-k, 0), (0, 0), (0, -k), (0, 0), (k, 0)]
        p = path(verts, ins, outs, True)
    return reverse(p) if direction == 3 else p


def ellipse_path(pos, size, direction=1):
    """AE ellipse: starts at the top, clockwise."""
    px, py = pos[0], pos[1]
    rx, ry = size[0] / 2, size[1] / 2
    kx, ky = rx * KAPPA, ry * KAPPA
    verts = [(px, py - ry), (px + rx, py), (px, py + ry), (px - rx, py)]
    ins = [(-kx, 0), (0, -ky), (kx, 0), (0, ky)]
    outs = [(kx, 0), (0, ky), (-kx, 0), (0, -ky)]
    p = path(verts, ins, outs, True)
    return reverse(p) if direction == 3 else p


def star_path(kind, points, pos, rot_deg, inner_r, outer_r, inner_round, outer_round, direction=1):
    """AE star (kind 1) / polygon (kind 2): first point straight up; roundness % gives tangents along the circle."""
    n = max(3, int(math.floor(points)))
    dirn = -1 if direction == 3 else 1
    verts, ins, outs = [], [], []
    ang = -math.pi / 2 + math.radians(rot_deg)
    if kind == 1:
        count = n * 2
        step = 2 * math.pi / count
        for i in range(count):
            outer = i % 2 == 0
            rad = outer_r if outer else inner_r
            rnd = (outer_round if outer else inner_round) / 100.0
            perim = 2 * math.pi * rad / (count * 2)
            x, y = rad * math.cos(ang), rad * math.sin(ang)
            ln = math.hypot(x, y) or 1.0
            ox, oy = (y / ln, -x / ln) if (x or y) else (0.0, 0.0)
            t = perim * rnd * dirn
            verts.append((x + pos[0], y + pos[1]))
            ins.append((ox * t, oy * t))
            outs.append((-ox * t, -oy * t))
            ang += step * dirn
    else:
        step = 2 * math.pi / n
        rad = outer_r
        rnd = outer_round / 100.0
        perim = 2 * math.pi * rad / (n * 4)
        for i in range(n):
            x, y = rad * math.cos(ang), rad * math.sin(ang)
            ln = math.hypot(x, y) or 1.0
            ox, oy = (y / ln, -x / ln) if (x or y) else (0.0, 0.0)
            t = perim * rnd * dirn
            verts.append((x + pos[0], y + pos[1]))
            ins.append((ox * t, oy * t))
            outs.append((-ox * t, -oy * t))
            ang += step * dirn
    return path(verts, ins, outs, True)


def _split(p0, c0, c1, p1, t=0.5):
    """de Casteljau split of one cubic segment (absolute points) -> left, right (absolute)."""
    def lerp(a, b):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    a, b, c = lerp(p0, c0), lerp(c0, c1), lerp(c1, p1)
    d, e = lerp(a, b), lerp(b, c)
    m = lerp(d, e)
    return (p0, a, d, m), (m, e, c, p1)


def resample(p, n):
    """Add vertices (splitting segments in half, in path order, breadth first — as AE does) up to n vertices."""
    v, i, o, c = [list(x) for x in p[:3]] + [p[3]]
    if len(v) == 1 and n > 1:                     # a shape keyed from a single point grows out of it
        return ([v[0]] * n, [(0.0, 0.0)] * n, [(0.0, 0.0)] * n, c)
    if len(v) >= n or len(v) < 2:
        return p
    while len(v) < n:
        segs = len(v) if c else len(v) - 1
        k = 0
        while k < segs and len(v) < n:
            j = (k + 1) % len(v)
            p0 = v[k]
            c0 = (p0[0] + o[k][0], p0[1] + o[k][1])
            p1 = v[j]
            c1 = (p1[0] + i[j][0], p1[1] + i[j][1])
            (_, a, d, m), (_, e, cc, _) = _split(p0, c0, c1, p1)
            o[k] = (a[0] - p0[0], a[1] - p0[1])
            i[j] = (cc[0] - p1[0], cc[1] - p1[1])
            v.insert(k + 1, m)
            i.insert(k + 1, (d[0] - m[0], d[1] - m[1]))
            o.insert(k + 1, (e[0] - m[0], e[1] - m[1]))
            k += 2
            segs += 1
    return (v, i, o, c)


def bounds(paths):
    xs, ys = [], []
    for v, i, o, _c in paths:
        for k, p in enumerate(v):
            xs += [p[0], p[0] + i[k][0], p[0] + o[k][0]]
            ys += [p[1], p[1] + i[k][1], p[1] + o[k][1]]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def polar(vec):
    """tangent vector -> (rotation rad, distance): the Rive CubicDetachedVertex handle encoding."""
    return math.atan2(vec[1], vec[0]), math.hypot(vec[0], vec[1])


def unwrap(prev, ang):
    """the angle equivalent to `ang` closest to `prev` (so a keyed handle does not spin the long way)."""
    if prev is None:
        return ang
    while ang - prev > math.pi:
        ang -= 2 * math.pi
    while ang - prev < -math.pi:
        ang += 2 * math.pi
    return ang


def reverse_open(p):
    """an open path walked from its last vertex (AE's Reverse Path for text on a path); a closed one as reverse()"""
    v, i, o, c = p
    if c or not v:
        return reverse(p)
    return (v[::-1], o[::-1], i[::-1], c)


def path_length(p, steps=64):
    """arc length of a path tuple (flattened cubics)"""
    v, i, o, c = p
    n = len(v)
    total = 0.0
    for k in range(n if c else n - 1):
        a, b = v[k], v[(k + 1) % n]
        c0 = (a[0] + o[k][0], a[1] + o[k][1])
        c1 = (b[0] + i[(k + 1) % n][0], b[1] + i[(k + 1) % n][1])
        prev = a
        for s in range(1, steps + 1):
            t = s / steps
            mt = 1 - t
            q = (mt ** 3 * a[0] + 3 * mt * mt * t * c0[0] + 3 * mt * t * t * c1[0] + t ** 3 * b[0],
                 mt ** 3 * a[1] + 3 * mt * mt * t * c0[1] + 3 * mt * t * t * c1[1] + t ** 3 * b[1])
            total += math.hypot(q[0] - prev[0], q[1] - prev[1])
            prev = q
    return total


def rsr(m):
    """linear part of m = R(phi) · diag(sx, sy) · R(theta) (exact, any 2x2: a skew too) -> (phi, sx, sy, theta)
    in radians; sy is negative for a mirrored matrix. Two nested Rive nodes (rotation + scale, then rotation)
    reproduce a skewed AE group."""
    a, b, c, d = m[0], m[1], m[2], m[3]          # columns (a, b) and (c, d)
    e, f = (a + d) / 2.0, (a - d) / 2.0
    g, h = (b + c) / 2.0, (b - c) / 2.0
    q, r = math.hypot(e, h), math.hypot(f, g)
    sx, sy = q + r, q - r
    a1, a2 = math.atan2(g, f), math.atan2(h, e)
    theta, phi = (a2 - a1) / 2.0, (a2 + a1) / 2.0
    return phi, sx, sy, theta
