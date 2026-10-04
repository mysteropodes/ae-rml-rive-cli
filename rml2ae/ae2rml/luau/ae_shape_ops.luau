-- After Effects shape operations for Rive path effects (ae2rml).
--
-- A path is read into contours of AE-style vertices (point + absolute in/out handles), changed, and written back.
-- Zig Zag, Offset Paths and Pucker & Bloat follow lottie-web's implementation (MIT, bodymovin); Twist, Roughen and
-- Wiggle Paths have no public reference and are approximations of what AE draws.

export type Vtx = { v: Vector, i: Vector, o: Vector }
export type Contour = { pts: { Vtx }, closed: boolean }
type Bez = { p0: Vector, p1: Vector, p2: Vector, p3: Vector }

local M = {}

local EPS = 1e-4
local ROUND_CORNER = 0.5519150244935105707435627

local function near(a: Vector, b: Vector): boolean
    return math.abs(a.x - b.x) < EPS and math.abs(a.y - b.y) < EPS
end

local function vtx(p: Vector): Vtx
    return { v = p, i = p, o = p }
end

local function dist(a: Vector, b: Vector): number
    return math.sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y))
end

local function lerpv(a: Vector, b: Vector, t: number): Vector
    return Vector.xy(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
end

local function polar(p: Vector, angle: number, len: number): Vector
    return Vector.xy(p.x + math.cos(angle) * len, p.y - math.sin(angle) * len)
end

-- ------------------------------------------------------------------ path <-> contours
function M.parse(path: PathData): { Contour }
    local out: { Contour } = {}
    local cur: Contour? = nil
    for k = 1, #path do
        local cmd = path[k]
        local t = cmd.type
        if t == "moveTo" then
            local c: Contour = { pts = { vtx(cmd[1]) }, closed = false }
            table.insert(out, c)
            cur = c
        elseif cur ~= nil then
            local c = cur :: Contour
            local last = c.pts[#c.pts]
            if t == "lineTo" then
                table.insert(c.pts, vtx(cmd[1]))
            elseif t == "cubicTo" then
                last.o = cmd[1]
                local p = cmd[3]
                table.insert(c.pts, { v = p, i = cmd[2], o = p })
            elseif t == "quadTo" then
                local q = cmd[1]
                local p = cmd[2]
                last.o = lerpv(last.v, q, 2 / 3)
                table.insert(c.pts, { v = p, i = lerpv(p, q, 2 / 3), o = p })
            elseif t == "close" then
                c.closed = true
                local n = #c.pts
                if n > 1 and near(c.pts[n].v, c.pts[1].v) then
                    c.pts[1].i = c.pts[n].i
                    table.remove(c.pts, n)
                end
                cur = nil
            end
        end
    end
    return out
end

local function seg(p: Path, a: Vtx, b: Vtx)
    if near(a.o, a.v) and near(b.i, b.v) then
        p:lineTo(b.v)
    else
        p:cubicTo(a.o, b.i, b.v)
    end
end

function M.emit(contours: { Contour }): Path
    local p = Path.new()
    for _, c in contours do
        local pts = c.pts
        local n = #pts
        if n > 0 then
            p:moveTo(pts[1].v)
            for k = 2, n do
                seg(p, pts[k - 1], pts[k])
            end
            if c.closed then
                if n > 1 then
                    seg(p, pts[n], pts[1])
                end
                p:close()
            end
        end
    end
    return p
end

-- ------------------------------------------------------------------ cubic helpers
local function bez(a: Vtx, b: Vtx): Bez
    local p1 = a.o
    local p2 = b.i
    if near(a.v, p1) then
        p1 = lerpv(a.v, b.v, 1 / 3)
    end
    if near(p2, b.v) then
        p2 = lerpv(a.v, b.v, 2 / 3)
    end
    return { p0 = a.v, p1 = p1, p2 = p2, p3 = b.v }
end

local function bpoint(b: Bez, t: number): Vector
    local u = 1 - t
    local a, c, d, e = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return Vector.xy(b.p0.x * a + b.p1.x * c + b.p2.x * d + b.p3.x * e, b.p0.y * a + b.p1.y * c + b.p2.y * d + b.p3.y * e)
end

local function bderiv(b: Bez, t: number): Vector
    local u = 1 - t
    local a, c, d = 3 * u * u, 6 * u * t, 3 * t * t
    return Vector.xy((b.p1.x - b.p0.x) * a + (b.p2.x - b.p1.x) * c + (b.p3.x - b.p2.x) * d,
        (b.p1.y - b.p0.y) * a + (b.p2.y - b.p1.y) * c + (b.p3.y - b.p2.y) * d)
end

local function bsplit(b: Bez, t: number): (Bez, Bez)
    local p10 = lerpv(b.p0, b.p1, t)
    local p11 = lerpv(b.p1, b.p2, t)
    local p12 = lerpv(b.p2, b.p3, t)
    local p20 = lerpv(p10, p11, t)
    local p21 = lerpv(p11, p12, t)
    local p3 = lerpv(p20, p21, t)
    return { p0 = b.p0, p1 = p10, p2 = p20, p3 = p3 }, { p0 = p3, p1 = p21, p2 = p12, p3 = b.p3 }
end

local function blength(b: Bez): number
    local len, prev = 0, b.p0
    for k = 1, 8 do
        local q = bpoint(b, k / 8)
        len += dist(prev, q)
        prev = q
    end
    return len
end

local function segments(c: Contour): { Bez }
    local out: { Bez } = {}
    local n = #c.pts
    local count = if c.closed then n else n - 1
    for k = 1, count do
        table.insert(out, bez(c.pts[k], c.pts[(k % n) + 1]))
    end
    return out
end

-- ------------------------------------------------------------------ Pucker & Bloat
function M.pucker(c: Contour, amount: number): Contour
    local n = #c.pts
    if n == 0 or amount == 0 then
        return c
    end
    local pct = amount / 100
    local cx, cy = 0, 0
    for _, p in c.pts do
        cx += p.v.x
        cy += p.v.y
    end
    cx /= n
    cy /= n
    local out: { Vtx } = {}
    for _, p in c.pts do
        table.insert(out, {
            v = Vector.xy(p.v.x + (cx - p.v.x) * pct, p.v.y + (cy - p.v.y) * pct),
            o = Vector.xy(p.o.x + (cx - p.o.x) * -pct, p.o.y + (cy - p.o.y) * -pct),
            i = Vector.xy(p.i.x + (cx - p.i.x) * -pct, p.i.y + (cy - p.i.y) * -pct),
        })
    end
    return { pts = out, closed = c.closed }
end

-- ------------------------------------------------------------------ Zig Zag
local function setPoint(out: { Vtx }, p: Vector, angle: number, dir: number, amp: number, outAmp: number, inAmp: number)
    local angO = angle - math.pi / 2
    local angI = angle + math.pi / 2
    local px = p.x + math.cos(angle) * dir * amp
    local py = p.y - math.sin(angle) * dir * amp
    table.insert(out, {
        v = Vector.xy(px, py),
        o = Vector.xy(px + math.cos(angO) * outAmp, py - math.sin(angO) * outAmp),
        i = Vector.xy(px + math.cos(angI) * inAmp, py - math.sin(angI) * inAmp),
    })
end

local function vAt(pts: { Vtx }, k: number): Vector
    return pts[(k % #pts) + 1].v
end

local function zzCorner(out: { Vtx }, pts: { Vtx }, cur: number, amp: number, freq: number, smooth: boolean, dir: number)
    local n = #pts
    local prevP = vAt(pts, if cur == 0 then n - 1 else cur - 1)
    local nextP = vAt(pts, cur + 1)
    local p = vAt(pts, cur)
    local vx, vy = nextP.x - prevP.x, nextP.y - prevP.y
    local angle = -math.atan2(-vx, vy)
    local prevDist = if smooth then dist(p, prevP) else 0
    local nextDist = if smooth then dist(p, nextP) else 0
    setPoint(out, p, angle, dir, amp, nextDist / ((freq + 1) * 2), prevDist / ((freq + 1) * 2))
end

local function zzSegment(out: { Vtx }, b: Bez, amp: number, freq: number, smooth: boolean, dir: number): number
    for k = 0, freq - 1 do
        local t = (k + 1) / (freq + 1)
        local d = if smooth then dist(b.p3, b.p0) else 0
        local dv = bderiv(b, t)
        setPoint(out, bpoint(b, t), math.atan2(dv.x, dv.y), dir, amp, d / ((freq + 1) * 2), d / ((freq + 1) * 2))
        dir = -dir
    end
    return dir
end

function M.zigzag(c: Contour, amp: number, ridges: number, smooth: boolean): Contour
    local pts = c.pts
    local n = #pts
    local count = if c.closed then n else n - 1
    local freq = math.max(0, math.floor(ridges + 0.5))
    if count <= 0 or amp == 0 then
        return c
    end
    local out: { Vtx } = {}
    local dir = -1
    zzCorner(out, pts, 0, amp, freq, smooth, dir)
    for k = 0, count - 1 do
        local b = bez(pts[k + 1], pts[((k + 1) % n) + 1])
        dir = zzSegment(out, b, amp, freq, smooth, -dir)
        zzCorner(out, pts, k + 1, amp, freq, smooth, dir)
    end
    return { pts = out, closed = c.closed }
end

-- ------------------------------------------------------------------ Offset Paths
local function lineIntersection(s1: Vector, e1: Vector, s2: Vector, e2: Vector): Vector?
    -- homogeneous coordinates: line = p x q, intersection = l1 x l2
    local l1x, l1y, l1z = s1.y - e1.y, e1.x - s1.x, s1.x * e1.y - s1.y * e1.x
    local l2x, l2y, l2z = s2.y - e2.y, e2.x - s2.x, s2.x * e2.y - s2.y * e2.x
    local rx, ry, rz = l1y * l2z - l1z * l2y, l1z * l2x - l1x * l2z, l1x * l2y - l1y * l2x
    -- parallel test on the sine of the angle between the lines: Rive's Vector is float32, so lottie's absolute
    -- 1e-5 lets nearly identical lines through and their "intersection" lands at 1e8 (measured on a star offset)
    local n1 = math.sqrt(l1x * l1x + l1y * l1y)
    local n2 = math.sqrt(l2x * l2x + l2y * l2y)
    if n1 < 1e-9 or n2 < 1e-9 or math.abs(rz) <= 1e-4 * n1 * n2 then
        return nil
    end
    return Vector.xy(rx / rz, ry / rz)
end

local function linearOffset(p1: Vector, p2: Vector, amount: number): (Vector, Vector)
    local angle = math.atan2(p2.x - p1.x, p2.y - p1.y)
    return polar(p1, angle, amount), polar(p2, angle, amount)
end

local function offsetSegment(b: Bez, amount: number): Bez
    local p0, p1a = linearOffset(b.p0, b.p1, amount)
    local p1b, p2b = linearOffset(b.p1, b.p2, amount)
    local p2a, p3 = linearOffset(b.p2, b.p3, amount)
    local p1 = lineIntersection(p0, p1a, p1b, p2b) or p1a
    local p2 = lineIntersection(p2a, p3, p1b, p2b) or p2a
    return { p0 = p0, p1 = p1, p2 = p2, p3 = p3 }
end

local function inflections(b: Bez): { number }
    local ax = -b.p0.x + 3 * b.p1.x - 3 * b.p2.x + b.p3.x
    local ay = -b.p0.y + 3 * b.p1.y - 3 * b.p2.y + b.p3.y
    local bx = 3 * b.p0.x - 6 * b.p1.x + 3 * b.p2.x
    local by = 3 * b.p0.y - 6 * b.p1.y + 3 * b.p2.y
    local cx = -3 * b.p0.x + 3 * b.p1.x
    local cy = -3 * b.p0.y + 3 * b.p1.y
    local denom = ay * bx - ax * by
    if math.abs(denom) <= 1e-5 then
        return {}
    end
    local tc = -0.5 * (ay * cx - ax * cy) / denom
    local sq = tc * tc - (1 / 3) * (by * cx - bx * cy) / denom
    if sq < 0 then
        return {}
    end
    local root = math.sqrt(sq)
    if math.abs(root) <= 1e-5 then
        return {}
    end
    local out: { number } = {}
    for _, r in { tc - root, tc + root } do
        if r > 0 and r < 1 then
            table.insert(out, r)
        end
    end
    return out
end

local function offsetSplit(b: Bez, amount: number): { Bez }
    local flex = inflections(b)
    if #flex == 0 then
        return { offsetSegment(b, amount) }
    end
    if #flex == 1 or math.abs(flex[2] - 1) < 1e-5 then
        local l, r = bsplit(b, flex[1])
        return { offsetSegment(l, amount), offsetSegment(r, amount) }
    end
    local l, rest = bsplit(b, flex[1])
    local m, r = bsplit(rest, (flex[2] - flex[1]) / (1 - flex[1]))
    return { offsetSegment(l, amount), offsetSegment(m, amount), offsetSegment(r, amount) }
end

type Box = { cx: number, cy: number, w: number, h: number, b: Bez, t: number, t1: number, t2: number }

local function box(b: Bez, t1: number, t2: number): Box
    local minx = math.min(b.p0.x, b.p1.x, b.p2.x, b.p3.x)
    local maxx = math.max(b.p0.x, b.p1.x, b.p2.x, b.p3.x)
    local miny = math.min(b.p0.y, b.p1.y, b.p2.y, b.p3.y)
    local maxy = math.max(b.p0.y, b.p1.y, b.p2.y, b.p3.y)
    return { cx = (minx + maxx) / 2, cy = (miny + maxy) / 2, w = maxx - minx, h = maxy - miny, b = b,
        t = (t1 + t2) / 2, t1 = t1, t2 = t2 }
end

local function intersects(d1: Box, d2: Box, depth: number, out: { { number } })
    if #out >= 4 then
        return
    end
    if not (math.abs(d1.cx - d2.cx) * 2 < d1.w + d2.w and math.abs(d1.cy - d2.cy) * 2 < d1.h + d2.h) then
        return
    end
    if depth >= 7 or (d1.w <= 2 and d1.h <= 2 and d2.w <= 2 and d2.h <= 2) then
        table.insert(out, { d1.t, d2.t })
        return
    end
    local a1, a2 = bsplit(d1.b, 0.5)
    local b1, b2 = bsplit(d2.b, 0.5)
    local A1, A2 = box(a1, d1.t1, d1.t), box(a2, d1.t, d1.t2)
    local B1, B2 = box(b1, d2.t1, d2.t), box(b2, d2.t, d2.t2)
    intersects(A1, B1, depth + 1, out)
    intersects(A1, B2, depth + 1, out)
    intersects(A2, B1, depth + 1, out)
    intersects(A2, B2, depth + 1, out)
end

local function firstIntersection(a: Bez, b: Bez): { number }?
    local out: { { number } } = {}
    intersects(box(a, 0, 1), box(b, 0, 1), 0, out)
    if #out > 0 and math.abs(out[1][1] - 1) < 1e-5 then
        table.remove(out, 1)
    end
    return out[1]
end

local function pruneSegment(a: { Bez }, b: { Bez }): ({ Bez }, { Bez })
    local outa = table.clone(a)
    local outb = table.clone(b)
    local hit = firstIntersection(a[#a], b[1])
    if hit then
        outa[#a] = (bsplit(a[#a], hit[1]))
        local _, r = bsplit(b[1], hit[2])
        outb[1] = r
    end
    if #a > 1 and #b > 1 then
        local hit2 = firstIntersection(a[1], b[#b])
        if hit2 then
            local l = bsplit(a[1], hit2[1])
            local _, r = bsplit(b[#b], hit2[2])
            return { l }, { r }
        end
    end
    return outa, outb
end

local function joinLines(out: { Vtx }, s1: Bez, s2: Bez, join: number, miter: number, amount: number): Vector
    local p0, p1 = s1.p3, s2.p0
    if join == 3 or near(p0, p1) then
        return p0
    end
    if join == 2 then
        local d1 = bderiv(s1, 1)
        local d2 = bderiv(s2, 0)
        local angleOut = -math.atan2(d1.y, d1.x)
        local angleIn = -math.atan2(d2.y, d2.x) + math.pi
        local center = lineIntersection(p0, polar(p0, angleOut + math.pi / 2, 100), p1, polar(p1, angleOut + math.pi / 2, 100))
        local radius = if center then dist(center, p0) else dist(p0, p1) / 2
        out[#out].o = polar(p0, angleOut, 2 * radius * ROUND_CORNER)
        table.insert(out, { v = p1, o = p1, i = polar(p1, angleIn, 2 * radius * ROUND_CORNER) })
        return p1
    end
    local t0 = if near(p0, s1.p2) then s1.p0 else s1.p2
    local t1 = if near(p1, s2.p1) then s2.p3 else s2.p1
    local hit = lineIntersection(t0, p0, p1, t1)
    -- AE's miter limit is a ratio of the offset amount (lottie compares it to a raw distance)
    if hit and dist(hit, p0) < miter * math.max(1, math.abs(amount)) then
        table.insert(out, vtx(hit))
        return hit
    end
    return p0
end

-- join: 1 miter, 2 round, 3 bevel
function M.offset(c: Contour, amount: number, join: number, miter: number): Contour
    local n = #c.pts
    if n < 2 or amount == 0 then
        return c
    end
    local segs = segments(c)
    local multi: { { Bez } } = {}
    for _, b in segs do
        table.insert(multi, offsetSplit(b, amount))
    end
    if not c.closed then
        for k = #segs, 1, -1 do
            local b = segs[k]
            table.insert(multi, offsetSplit({ p0 = b.p3, p1 = b.p2, p2 = b.p1, p3 = b.p0 }, amount))
        end
    end
    for k = 2, #multi do
        multi[k - 1], multi[k] = pruneSegment(multi[k - 1], multi[k])
    end
    if #multi > 1 then
        multi[#multi], multi[1] = pruneSegment(multi[#multi], multi[1])
    end
    local out: { Vtx } = {}
    local lastPoint: Vector? = nil
    local lastSeg: Bez? = nil
    for _, ms in multi do
        if lastSeg then
            lastPoint = joinLines(out, lastSeg :: Bez, ms[1], join, miter, amount)
        end
        lastSeg = ms[#ms]
        for _, s in ms do
            if lastPoint and near(s.p0, lastPoint :: Vector) and #out > 0 then
                out[#out].o = s.p1
            else
                table.insert(out, { v = s.p0, o = s.p1, i = s.p0 })
            end
            table.insert(out, { v = s.p3, o = s.p3, i = s.p2 })
            lastPoint = s.p3
        end
    end
    if lastSeg and #multi > 0 then
        joinLines(out, lastSeg :: Bez, multi[1][1], join, miter, amount)
    end
    -- an offset path is always closed (an open path is walked there and back)
    local m = #out
    if m > 1 and near(out[m].v, out[1].v) then
        out[1].i = out[m].i
        table.remove(out, m)
    end
    return { pts = out, closed = true }
end

-- ------------------------------------------------------------------ subdivision (Twist, Roughen, Wiggle)
-- each segment cut in pieces of about `step` units (at least `minPieces`), handles kept exact (de Casteljau)
function M.subdivide(c: Contour, step: number, minPieces: number, curvesOnly: boolean?): Contour
    local n = #c.pts
    if n < 2 then
        return c
    end
    local out: { Vtx } = { { v = c.pts[1].v, i = c.pts[1].i, o = c.pts[1].o } }
    local segs = segments(c)
    for k, b in segs do
        local straight = near(c.pts[k].o, c.pts[k].v) and near(c.pts[(k % n) + 1].i, c.pts[(k % n) + 1].v)
        local pieces = if straight and curvesOnly then 1 else math.clamp(math.ceil(blength(b) / math.max(step, 0.5)), minPieces, 64)
        local rest = b
        for j = 1, pieces do
            local left: Bez
            if j == pieces then
                left = rest
            else
                local r: Bez
                left, r = bsplit(rest, 1 / (pieces - j + 1))
                rest = r
            end
            local last = out[#out]
            if not straight then
                last.o = left.p1
            end
            local isEnd = j == pieces and k == #segs and c.closed
            if isEnd then
                if not straight then
                    out[1].i = left.p2
                end
            else
                local nextV = if j == pieces then c.pts[(k % n) + 1] else nil
                table.insert(out, {
                    v = left.p3,
                    i = if straight then left.p3 else left.p2,
                    o = if nextV then nextV.o else left.p3,
                })
            end
        end
    end
    return { pts = out, closed = c.closed }
end

function M.twist(c: Contour, angleDeg: number, center: Vector): Contour
    -- Measured in After Effects 26.5 (radial lines, off-centre lines, a far second path): each path turns about the
    -- centre of ITS OWN bounding box (+ the Center parameter), by angle · (1 - d / R), R = its farthest point from
    -- that centre — linear falloff, clockwise for a positive angle (mean error 0.16°, max 1.2° over 3 probes).
    if angleDeg == 0 or #c.pts == 0 then
        return c
    end
    local sub = M.subdivide(c, 6, 8)
    local minx: number = math.huge
    local miny: number = math.huge
    local maxx: number = -math.huge
    local maxy: number = -math.huge
    for _, p in sub.pts do
        local x: number, y: number = p.v.x, p.v.y
        minx = math.min(minx, x)
        maxx = math.max(maxx, x)
        miny = math.min(miny, y)
        maxy = math.max(maxy, y)
    end
    local cx: number = (minx + maxx) / 2 + center.x
    local cy: number = (miny + maxy) / 2 + center.y
    local R: number = 0
    for _, p in sub.pts do
        local dx: number, dy: number = p.v.x - cx, p.v.y - cy
        R = math.max(R, math.sqrt(dx * dx + dy * dy))
    end
    if R <= 0 then
        return c
    end
    local a = math.rad(angleDeg)
    local function rot(q: Vector): Vector
        local dx, dy = q.x - cx, q.y - cy
        local t = a * math.max(0, 1 - math.sqrt(dx * dx + dy * dy) / R)
        local cs, sn = math.cos(t), math.sin(t)
        return Vector.xy(cx + dx * cs - dy * sn, cy + dx * sn + dy * cs)
    end
    local out: { Vtx } = {}
    for _, p in sub.pts do
        table.insert(out, { v = rot(p.v), i = rot(p.i), o = rot(p.o) })
    end
    return { pts = out, closed = c.closed }
end

-- deterministic value noise in [-1, 1]
local function hash(n: number): number
    local s = math.sin(n * 127.1 + 311.7) * 43758.5453
    return (s - math.floor(s)) * 2 - 1
end

local function noise1(x: number, seed: number): number
    local i = math.floor(x)
    local f = x - i
    local u = f * f * (3 - 2 * f)
    return hash(i + seed * 57.3) * (1 - u) + hash(i + 1 + seed * 57.3) * u
end

-- Wiggle Paths (AE 26: "ADBE Vector Filter - Roughen"). Measured in AE 26.5: Detail = points added PER SEGMENT
-- (evenly spaced, whatever the segment's length), each point pushed along the path's normal by up to ±Size/2, the
-- original vertices included. AE's random sequence is not public: same structure and amplitude, other draws.
-- `phase` (keyed: wiggles/second × time + temporal phase) moves the noise; correlation smooths it between points.
function M.roughen(c: Contour, size: number, detail: number, smooth: boolean, seed: number, phase: number,
    correlation: number): Contour
    local n = #c.pts
    if size == 0 or n < 2 then
        return c
    end
    local pieces = math.max(1, math.floor(detail + 0.5) + 1)
    local segs = segments(c)
    local pos: { Vector } = {}
    local tan: { Vector } = {}
    for _, b in segs do
        for j = 0, pieces - 1 do
            local t = j / pieces
            table.insert(pos, bpoint(b, t))
            table.insert(tan, bderiv(b, t))
        end
    end
    if not c.closed then
        local b = segs[#segs]
        table.insert(pos, b.p3)
        table.insert(tan, bderiv(b, 1))
    end
    local m = #pos
    local corr = math.clamp(correlation / 100, 0, 0.99)
    local out: { Vtx } = {}
    for i = 1, m do
        -- normal: perpendicular of the neighbours' chord at a vertex, of the tangent inside a segment
        local prev = pos[if i > 1 then i - 1 else (if c.closed then m else 1)]
        local nxt = pos[if i < m then i + 1 else (if c.closed then 1 else m)]
        local d = Vector.xy(nxt.x - prev.x, nxt.y - prev.y)
        if d.x * d.x + d.y * d.y < 1e-12 then
            d = tan[i]
        end
        local len = math.sqrt(d.x * d.x + d.y * d.y)
        local nx, ny = 0, 0
        if len > 1e-9 then
            nx, ny = -d.y / len, d.x / len
        end
        -- each point: its own random walk in time, blended with a noise shared by its neighbours (correlation)
        local own = noise1(phase + i * 7.31 + seed * 3.1, seed + 1)
        local shared = noise1(i * 0.35 + phase + seed * 13.7, seed + 5)
        local k = own * (1 - corr) + shared * corr
        local p = Vector.xy(pos[i].x + nx * k * size / 2, pos[i].y + ny * k * size / 2)
        table.insert(out, vtx(p))
    end
    if smooth then
        for i = 1, m do
            local a = out[if i > 1 then i - 1 else (if c.closed then m else 1)].v
            local b = out[if i < m then i + 1 else (if c.closed then 1 else m)].v
            local tx, ty = (b.x - a.x) / 6, (b.y - a.y) / 6
            local v = out[i].v
            out[i].i = Vector.xy(v.x - tx, v.y - ty)
            out[i].o = Vector.xy(v.x + tx, v.y + ty)
        end
    end
    return { pts = out, closed = c.closed }
end

return M
