-- AE "Zig Zag" shape operation (ae2rml). Inputs keep AE's names and units.
local ops = require('ae_shape_ops')

type ZigZag = {
    size: Input<number>,     -- AE Size (px)
    ridges: Input<number>,   -- AE Ridges per segment
    points: Input<number>,   -- AE Points: 1 corner, 2 smooth
}

local function update(self: ZigZag, path: PathData, node: NodeReadData): PathData
    local cs = ops.parse(path)
    for k, c in cs do
        cs[k] = ops.zigzag(c, self.size, self.ridges, self.points >= 1.5)
    end
    return ops.emit(cs)
end

return function(): PathEffect<ZigZag>
    return { size = 10, ridges = 5, points = 1, update = update }
end
