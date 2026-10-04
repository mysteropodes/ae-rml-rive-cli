-- AE "Pucker & Bloat" shape operation (ae2rml).
local ops = require('ae_shape_ops')

type Pucker = {
    amount: Input<number>,   -- AE Amount (%): negative = pucker, positive = bloat
}

local function update(self: Pucker, path: PathData, node: NodeReadData): PathData
    local cs = ops.parse(path)
    for k, c in cs do
        cs[k] = ops.pucker(c, self.amount)
    end
    return ops.emit(cs)
end

return function(): PathEffect<Pucker>
    return { amount = 0, update = update }
end
