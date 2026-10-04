-- AE "Wiggle Paths" shape operation (match name "ADBE Vector Filter - Roughen"; structure measured in AE 26.5, random draws differ).
-- Wiggle Paths = Roughen with `phase` keyed over time (wiggles per second x time + temporal phase).
local ops = require('ae_shape_ops')

type Roughen = {
    size: Input<number>,         -- AE Size (px)
    detail: Input<number>,       -- AE Detail (points added per segment)
    points: Input<number>,       -- AE Points: 1 corner, 2 smooth
    seed: Input<number>,         -- AE Random Seed
    phase: Input<number>,        -- time phase (Wiggle Paths)
    correlation: Input<number>,  -- AE Correlation (%)
}

local function update(self: Roughen, path: PathData, node: NodeReadData): PathData
    local cs = ops.parse(path)
    for k, c in cs do
        cs[k] = ops.roughen(c, self.size, self.detail, self.points >= 1.5, self.seed, self.phase, self.correlation)
    end
    return ops.emit(cs)
end

return function(): PathEffect<Roughen>
    return { size = 5, detail = 10, points = 1, seed = 0, phase = 0, correlation = 50, update = update }
end
