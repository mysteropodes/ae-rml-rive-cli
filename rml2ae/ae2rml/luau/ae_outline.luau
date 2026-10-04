-- ae2rml: the area a stroke paints, as a filled path — for a track matte drawn with strokes. Rive clips with a path's
-- fill region only, AE with the matte's alpha: a stroke as wide as its circle's diameter, trimmed, is the classic
-- clock wipe (rig-test: "Calque de forme 7" revealed the next shot through its stroke, Rive showed a small disc).
-- Placed after the stroke's Trim Paths / dashes, in a transparent Fill of the matte source.
-- The strip is cut in small quads, each split in two triangles wound the same way: overlapping pieces add up under
-- the non-zero rule, so a stroke wider than its curve's radius covers the centre instead of cancelling there.

type Outline = {
    width: Input<number>,
    cap: Input<number>,       -- 0 butt, 1 round, 2 square
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

-- half disc beyond an open end: centre p, going along dir (unit), radius r
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

local function update(self: Outline, path: PathData, node: NodeReadData): PathData
    local hw = math.max(0, self.width) * 0.5
    local out = Path.new()
    if hw <= 1e-6 then
        return out
    end
    local contour = path:contours()
    while contour do
        local c = contour :: ContourMeasure
        local len = c.length
        if len > 1e-6 then
            local n = math.clamp(math.ceil(len / 3), 12, 720)
            local P: { Vector } = {}
            local L: { Vector } = {}
            local R: { Vector } = {}
            local T: { Vector } = {}
            for i = 0, n do
                local d = if c.isClosed and i == n then 0 else len * i / n
                local p, t = c:positionAndTangent(d)
                local tn = if Vector.length(t) > 1e-9 then Vector.normalized(t) else Vector.xy(1, 0)
                local nx = Vector.xy(-tn.y, tn.x)
                table.insert(P, p)
                table.insert(T, tn)
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
                    local e1, s1 = T[last] * hw, T[1] * -hw
                    tri(out, L[last], L[last] + e1, R[last] + e1)
                    tri(out, L[last], R[last] + e1, R[last])
                    tri(out, L[1], L[1] + s1, R[1] + s1)
                    tri(out, L[1], R[1] + s1, R[1])
                elseif self.cap > 0.5 then
                    roundCap(out, P[last], T[last], hw)
                    roundCap(out, P[1], T[1] * -1, hw)
                end
            end
        end
        contour = c.next
    end
    return out
end

return function(): PathEffect<Outline>
    return { width = 1, cap = 0, update = update }
end
