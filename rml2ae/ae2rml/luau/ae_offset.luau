-- AE "Offset Paths" shape operation (ae2rml).
local ops = require('ae_shape_ops')

type Offset = {
    amount: Input<number>,      -- AE Amount (px, positive = outwards on a clockwise path)
    join: Input<number>,        -- AE Line Join: 1 miter, 2 round, 3 bevel
    miterLimit: Input<number>,  -- AE Miter Limit
    copies: Input<number>,      -- AE Copies
    copyOffset: Input<number>,  -- AE Copy Offset
}

local function update(self: Offset, path: PathData, node: NodeReadData): PathData
    local cs = ops.parse(path)
    local out: { ops.Contour } = {}
    local n = math.max(1, math.floor(self.copies + 0.5))
    for _, c in cs do
        for k = 0, n - 1 do
            -- copies are concentric offsets (1x, 2x … the amount), shifted by Copy Offset (approximation)
            local amt = if n == 1 then self.amount else self.amount * (k + 1 + self.copyOffset)
            table.insert(out, ops.offset(c, amt, math.floor(self.join + 0.5), self.miterLimit))
        end
    end
    return ops.emit(out)
end

return function(): PathEffect<Offset>
    return { amount = 10, join = 1, miterLimit = 4, copies = 1, copyOffset = 0, update = update }
end
