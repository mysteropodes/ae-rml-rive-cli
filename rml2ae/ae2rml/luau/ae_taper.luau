-- ae2rml: AE stroke Taper, as the filled band of the stroke (Rive strokes have one width). In a Fill that replaces
-- the Stroke, after its Trim Paths. Measured in AE 26.5 on an open curve:
--   width(d) = W · fs(d) · fe(d)   (the start and end tapers multiply where they overlap: 70 % + 70 % → 51 % mid)
--   fs = sw + (1 - sw) · ease(d / startLen) on the first startLen of each contour, 1 after (fe mirrored at the end)
--   ease = the cubic bezier (0,0) (0,e) (1-e,1) (1,1) — e = 0 is linear (rms 0.64 px against AE's band)
-- Lengths are fractions of the contour (units = 0) or pixels (units = 1). The band is cut in small quads, each split
-- in two triangles wound the same way (see ae_outline: overlaps add up under the non-zero rule).

type Taper = {
    width: Input<number>,
    cap: Input<number>,          -- 0 butt, 1 round, 2 square (drawn at a tip that keeps a width)
    units: Input<number>,        -- 0 fractions of the length, 1 pixels
    startLen: Input<number>,
    endLen: Input<number>,
    startWidth: Input<number>,   -- fraction of the width at the start tip
    endWidth: Input<number>,
    startEase: Input<number>,    -- 0..1
    endEase: Input<number>,
}

local function tri(out: Path, a: Vector, b: Vector, c: Vector)
    local cr = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
    if math.abs(cr) < 1e-9 then
        return
    end
    out:moveTo(a)
    if cr > 0 then
        out:lineTo(b)
        out:lineTo(c)
    else
        out:lineTo(c)
        out:lineTo(b)
    end
    out:close()
end

local function roundCap(out: Path, p: Vector, dir: Vector, r: number)
    local n = Vector.xy(-dir.y, dir.x)
    local steps = 12
    local prev = p + n * r
    for i = 1, steps do
        local a = math.pi * i / steps
        local q = p + n * (r * math.cos(a)) + dir * (r * math.sin(a))
        tri(out, p, prev, q)
        prev = q
    end
end

-- y of the bezier (0,0) (0,e) (1-e,1) (1,1) at x = u
local function ease(u: number, e: number): number
    if u <= 0 then
        return 0
    end
    if u >= 1 then
        return 1
    end
    if e <= 1e-6 then
        return u
    end
    local x2 = 1 - e
    local lo, hi = 0, 1
    for _ = 1, 30 do
        local t = (lo + hi) * 0.5
        local x = 3 * (1 - t) * t * t * x2 + t * t * t
        if x < u then
            lo = t
        else
            hi = t
        end
    end
    local t = (lo + hi) * 0.5
    return 3 * (1 - t) * (1 - t) * t * e + 3 * (1 - t) * t * t + t * t * t
end

local function update(self: Taper, path: PathData, node: NodeReadData): PathData
    local W = math.max(0, self.width)
    local out = Path.new()
    if W <= 1e-6 then
        return out
    end
    local contour = path:contours()
    while contour do
        local c = contour :: ContourMeasure
        local len = c.length
        if len > 1e-6 then
            local sl = if self.units > 0.5 then self.startLen else self.startLen * len
            local el = if self.units > 0.5 then self.endLen else self.endLen * len
            local function half(d: number): number
                local fs, fe = 1, 1
                if sl > 1e-6 and d < sl then
                    fs = self.startWidth + (1 - self.startWidth) * ease(d / sl, self.startEase)
                end
                if el > 1e-6 and len - d < el then
                    fe = self.endWidth + (1 - self.endWidth) * ease((len - d) / el, self.endEase)
                end
                return W * 0.5 * fs * fe
            end
            local n = math.clamp(math.ceil(len / 2), 24, 1200)
            local P: { Vector } = {}
            local L: { Vector } = {}
            local R: { Vector } = {}
            local T: { Vector } = {}
            local H: { number } = {}
            for i = 0, n do
                local d = len * i / n
                local p, t = c:positionAndTangent(if c.isClosed and i == n then 0 else d)
                local tn = if Vector.length(t) > 1e-9 then Vector.normalized(t) else Vector.xy(1, 0)
                local nx = Vector.xy(-tn.y, tn.x)
                local hw = half(d)
                table.insert(P, p)
                table.insert(T, tn)
                table.insert(H, hw)
                table.insert(L, p + nx * hw)
                table.insert(R, p - nx * hw)
            end
            for i = 1, n do
                tri(out, L[i], L[i + 1], R[i + 1])
                tri(out, L[i], R[i + 1], R[i])
            end
            if not c.isClosed then
                local last = n + 1
                if self.cap > 1.5 then
                    local e1, s1 = T[last] * H[last], T[1] * -H[1]
                    tri(out, L[last], L[last] + e1, R[last] + e1)
                    tri(out, L[last], R[last] + e1, R[last])
                    tri(out, L[1], L[1] + s1, R[1] + s1)
                    tri(out, L[1], R[1] + s1, R[1])
                elseif self.cap > 0.5 then
                    if H[last] > 1e-6 then
                        roundCap(out, P[last], T[last], H[last])
                    end
                    if H[1] > 1e-6 then
                        roundCap(out, P[1], T[1] * -1, H[1])
                    end
                end
            end
        end
        contour = c.next
    end
    return out
end

return function(): PathEffect<Taper>
    return {
        width = 1, cap = 0, units = 0, startLen = 0, endLen = 0, startWidth = 0, endWidth = 0,
        startEase = 0, endEase = 0, update = update,
    }
end
