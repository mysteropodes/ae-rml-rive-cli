-- ae2rml: an After Effects plane seen through a 3D camera (or a Corner Pin), as a path effect.
-- The converter draws the layer with its flattened 2D transform; h11…h33 is the artboard-space homography that
-- moves those points to where After Effects draws them (keyed per frame). Straight segments stay exact (a homography
-- keeps lines straight); curves are cut in small pieces whose control points are projected.
local ops = require('ae_shape_ops')

type Project = {
    h11: Input<number>, h12: Input<number>, h13: Input<number>,
    h21: Input<number>, h22: Input<number>, h23: Input<number>,
    h31: Input<number>, h32: Input<number>, h33: Input<number>,
}

local function update(self: Project, path: PathData, node: NodeReadData): PathData
    local W = node.worldTransform
    local Wi = W:invert()
    if Wi == nil then
        return path
    end
    local inv = Wi :: Mat2D
    local function map(p: Vector): Vector
        local q = W * p
        local w = self.h31 * q.x + self.h32 * q.y + self.h33
        if math.abs(w) < 1e-9 then
            w = if w < 0 then -1e-9 else 1e-9
        end
        local r = Vector.xy((self.h11 * q.x + self.h12 * q.y + self.h13) / w, (self.h21 * q.x + self.h22 * q.y + self.h23) / w)
        return inv * r
    end
    local cs = ops.parse(path)
    for k, c in cs do
        local sub = ops.subdivide(c, 24, 4, true)
        for _, v in sub.pts do
            v.v, v.i, v.o = map(v.v), map(v.i), map(v.o)
        end
        cs[k] = sub
    end
    return ops.emit(cs)
end

return function(): PathEffect<Project>
    return { h11 = 1, h12 = 0, h13 = 0, h21 = 0, h22 = 1, h23 = 0, h31 = 0, h32 = 0, h33 = 1, update = update }
end
