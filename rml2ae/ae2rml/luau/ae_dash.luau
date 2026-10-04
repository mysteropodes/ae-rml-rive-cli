-- ae2rml: After Effects stroke dashes, as a path effect placed after the Trim Paths of a stroke. Used where Rive's
-- DashPath goes wrong: after a TrimPath, a first dash shorter than ~10 units (or any offset) swallows the next gap
-- (measured, CLI 1.2.0). AE semantics: the pattern restarts on each contour, shifted back by the offset; where a
-- Trim Paths has Start > End, AE runs the kept part backwards, so the pattern starts from the contour's end (reverse).

type Dash = {
    d1: Input<number>, g1: Input<number>,
    d2: Input<number>, g2: Input<number>,
    d3: Input<number>, g3: Input<number>,
    count: Input<number>,     -- dash/gap pairs used (1..3)
    offset: Input<number>,
    reverse: Input<number>,   -- > 0.5: lay the pattern from the end of each contour (AE trim with Start > End)
}

local function update(self: Dash, path: PathData, node: NodeReadData): PathData
    local pat: { number } = { self.d1, self.g1 }
    if self.count >= 2 then
        table.insert(pat, self.d2)
        table.insert(pat, self.g2)
    end
    if self.count >= 3 then
        table.insert(pat, self.d3)
        table.insert(pat, self.g3)
    end
    local period = 0
    for _, v in pat do
        period += math.max(0, v)
    end
    if period <= 1e-6 then
        return path
    end
    local out = Path.new()
    local contour = path:contours()
    while contour do
        local c = contour :: ContourMeasure
        local len = c.length
        -- where the pattern stands at s = 0: AE's offset moves the pattern back, phase = +offset (mod period)
        local phase = self.offset % period
        local k, acc = 1, 0
        while acc + math.max(0, pat[k]) <= phase do
            acc += math.max(0, pat[k])
            k = (k % #pat) + 1
        end
        local s = 0
        local left = math.max(0, pat[k]) - (phase - acc)
        local guard = 0
        while s < len and guard < 100000 do
            guard += 1
            local e = math.min(len, s + left)
            if k % 2 == 1 and e > s then
                if self.reverse > 0.5 then
                    c:extract(len - e, len - s, out, true)
                else
                    c:extract(s, e, out, true)
                end
            end
            s = e
            k = (k % #pat) + 1
            left = math.max(0, pat[k])
        end
        contour = c.next
    end
    return out
end

return function(): PathEffect<Dash>
    return { d1 = 10, g1 = 10, d2 = 0, g2 = 0, d3 = 0, g3 = 0, count = 1, offset = 0, reverse = 0, update = update }
end
