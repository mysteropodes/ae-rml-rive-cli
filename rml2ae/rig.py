"""Rigging: forward kinematics of bones, linear-blend skinning baked into path keyframes, constraint detection.

Rive semantics (rive docs rigging): a RootBone takes x/y; a Bone sits at its parent's tip (parent.length, 0) and
turns by `rotation` (radians, relative). A Skin nests inside the deformed PointsPath / Mesh, holds one Tendon per bone
(bind transform of the bone), each vertex carries Weight/CubicWeight (4 packed bytes: indices = tendonIndex+1,
values = influence/255). Deformed vertex (world) = sum_i w_i * BoneWorld_i * inverse(TendonBind_i) * SkinBind * v.
"""
import math

from .model import is_a


def mat(xx=1.0, xy=0.0, yx=0.0, yy=1.0, tx=0.0, ty=0.0):
    return (xx, xy, yx, yy, tx, ty)


def mul(a, b):
    """a * b (apply b first, then a) for 2x3 matrices stored as (xx, xy, yx, yy, tx, ty), Rive convention:
    x unit vector = (xx, xy), y unit vector = (yx, yy)."""
    axx, axy, ayx, ayy, atx, aty = a
    bxx, bxy, byx, byy, btx, bty = b
    return (axx * bxx + ayx * bxy, axy * bxx + ayy * bxy,
            axx * byx + ayx * byy, axy * byx + ayy * byy,
            axx * btx + ayx * bty + atx, axy * btx + ayy * bty + aty)


def apply(m, x, y):
    xx, xy, yx, yy, tx, ty = m
    return (xx * x + yx * y + tx, xy * x + yy * y + ty)


def invert(m):
    xx, xy, yx, yy, tx, ty = m
    det = xx * yy - yx * xy
    if abs(det) < 1e-12:
        return mat()
    ixx, ixy, iyx, iyy = yy / det, -xy / det, -yx / det, xx / det
    return (ixx, ixy, iyx, iyy, -(ixx * tx + iyx * ty), -(ixy * tx + iyy * ty))


def trs(x, y, rot, sx, sy):
    c, s = math.cos(rot), math.sin(rot)
    return (c * sx, s * sx, -s * sy, c * sy, x, y)


def local_at(el, frame, ctx):
    """Local transform of a TransformComponent (Node, Shape, Bone, RootBone...) at a frame, keys applied."""
    def v(prop, default):
        k = ctx.keys(el.id, prop) if el.id else None
        from .convert import value_at
        return value_at(k, frame) if k else el.num(prop, default)
    rot, sx, sy = v("rotation", 0.0), v("scaleX", 1.0), v("scaleY", 1.0)
    if el.tag == "Bone":
        # positioned at the parent bone's tip
        par = el.parent
        length = 0.0
        if par is not None and is_a(par.tag, "Bone"):
            kl = ctx.keys(par.id, "length") if par.id else None
            from .convert import value_at
            length = value_at(kl, frame) if kl else par.num("length", 0.0)
        x, y = length, 0.0
    else:
        x, y = v("x", 0.0), v("y", 0.0)
    return trs(x, y, rot, sx, sy)


def world_at(el, frame, ctx, cache=None):
    """World transform (artboard space) of a component at a frame."""
    cache = cache if cache is not None else {}
    key = (id(el), frame)
    if key in cache:
        return cache[key]
    if el is None or el.tag == "Artboard":
        return mat()
    parent = el.parent
    while parent is not None and parent.tag != "Artboard" and not is_a(parent.tag, "TransformComponent"):
        parent = parent.parent
    pm = world_at(parent, frame, ctx, cache) if (parent is not None and parent.tag != "Artboard") else mat()
    m = mul(pm, local_at(el, frame, ctx)) if is_a(el.tag, "TransformComponent") else pm
    cache[key] = m
    return m


def unpack(values, indices):
    """Packed weights -> [(tendon index, weight 0..1)]"""
    out = []
    for slot in range(4):
        idx = (int(indices) >> (8 * slot)) & 255
        val = (int(values) >> (8 * slot)) & 255
        if idx > 0 and val > 0:
            out.append((idx - 1, val / 255.0))
    return out


def bake_skinned_path(pp, skin, ctx, frames):
    """Skinned PointsPath -> per frame: (vertices world, in tangents, out tangents) lists."""
    from .convert import value_at
    by_id = ctx.conv.p.by_id
    tendons = [t for t in skin.children if t.tag == "Tendon"]
    bind_inv = []
    for t in tendons:
        bind_inv.append(invert(mat(t.num("xx", 1), t.num("xy", 0), t.num("yx", 0), t.num("yy", 1), t.num("tx", 0), t.num("ty", 0))))
    skin_bind = mat(skin.num("xx", 1), skin.num("xy", 0), skin.num("yx", 0), skin.num("yy", 1), skin.num("tx", 0), skin.num("ty", 0))
    verts = [v for v in pp.children if is_a(v.tag, "Vertex")]
    out = []
    cache = {}
    for f in frames:
        bones = []
        for t in tendons:
            b = by_id.get(t.get("boneId"))
            bones.append(world_at(b, f, ctx, cache) if b is not None else mat())
        deform = [mul(bw, bi) for bw, bi in zip(bones, bind_inv)]
        V, I, O = [], [], []
        for v in verts:
            x, y = v.num("x"), v.num("y")
            kx, ky = (ctx.keys(v.id, "x"), ctx.keys(v.id, "y")) if v.id else (None, None)
            if kx:
                x = value_at(kx, f)
            if ky:
                y = value_at(ky, f)
            # handles (absolute local points) as in convert.path_shape
            if v.tag == "CubicDetachedVertex":
                ir, idist, orr, odist = v.num("inRotation"), v.num("inDistance"), v.num("outRotation"), v.num("outDistance")
                pin = (x + math.cos(ir) * idist, y + math.sin(ir) * idist)
                pout = (x + math.cos(orr) * odist, y + math.sin(orr) * odist)
            elif v.tag == "CubicMirroredVertex":
                r, d = v.num("rotation"), v.num("distance")
                pin, pout = (x - math.cos(r) * d, y - math.sin(r) * d), (x + math.cos(r) * d, y + math.sin(r) * d)
            elif v.tag == "CubicAsymmetricVertex":
                r, di, do = v.num("rotation"), v.num("inDistance"), v.num("outDistance")
                pin, pout = (x - math.cos(r) * di, y - math.sin(r) * di), (x + math.cos(r) * do, y + math.sin(r) * do)
            else:
                pin = pout = None
            w = v.find("Weight") or v.find("CubicWeight")

            def blend(px, py, values, indices):
                bx, by = apply(skin_bind, px, py)
                if w is None:
                    return (bx, by)
                sx = sy = 0.0
                tot = 0.0
                for ti, wt in unpack(values, indices):
                    if ti < len(deform):
                        dx, dy = apply(deform[ti], bx, by)
                        sx += dx * wt
                        sy += dy * wt
                        tot += wt
                if tot <= 0:
                    return (bx, by)
                return (sx / tot, sy / tot)
            wv = (w.num("values", 255), w.num("indices", 1)) if w is not None else (255, 1)
            P = blend(x, y, *wv)
            V.append([P[0], P[1]])
            if pin is not None:
                wi = (w.num("inValues", wv[0]), w.num("inIndices", wv[1])) if (w is not None and w.tag == "CubicWeight") else wv
                wo = (w.num("outValues", wv[0]), w.num("outIndices", wv[1])) if (w is not None and w.tag == "CubicWeight") else wv
                PI, PO = blend(pin[0], pin[1], *wi), blend(pout[0], pout[1], *wo)
                I.append([PI[0] - P[0], PI[1] - P[1]])
                O.append([PO[0] - P[0], PO[1] - P[1]])
            else:
                I.append([0, 0])
                O.append([0, 0])
        out.append((V, I, O))
    return out


def constraints_in(el):
    """Constraint elements on `el` or any descendant."""
    return [c for e in el.iter() for c in e.children if is_a(c.tag, "Constraint")]


def has_skin(el):
    return any(c.tag == "Skin" for e in el.iter() for c in e.children)
