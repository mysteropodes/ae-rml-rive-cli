-- AE "Twist" shape operation (ae2rml): measured in AE 26.5, see ae_shape_ops.twist.
local ops = require('ae_shape_ops')

type Twist = {
    angle: Input<number>,     -- AE Angle (degrees)
    centerX: Input<number>,   -- AE Center
    centerY: Input<number>,
}

local function update(self: Twist, path: PathData, node: NodeReadData): PathData
    local cs = ops.parse(path)
    local center = Vector.xy(self.centerX, self.centerY)
    for k, c in cs do
        cs[k] = ops.twist(c, self.angle, center)
    end
    return ops.emit(cs)
end

return function(): PathEffect<Twist>
    return { angle = 0, centerX = 0, centerY = 0, update = update }
end
