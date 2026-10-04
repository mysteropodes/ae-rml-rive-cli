-- ae2rml: a precomp on an After Effects 3D plane (or Corner Pin), in exact perspective — what AE does: the nested
-- comp is rendered flat (here: its artboard into a 2D canvas, at the remapped time), then that image is mapped on
-- the plane through the keyed homography h11…h33 (layer space -> layer space) with a 16×16 mesh.
-- CLI 1.2.0: canvas frames are drawn inside draw() (the former drawCanvas callback is gone).

type PlaneArtboard = {
    artboard: Input<Artboard<nil>>,
    animation: Input<string>,   -- the nested artboard's animation
    time: Input<number>,        -- its time in seconds (keyed: start time, stretch, time remap)
    width: Input<number>,       -- the precomp size in layer units
    height: Input<number>,
    resolution: Input<number>,  -- canvas pixels per unit (more when the plane is seen enlarged)
    blend: Input<string>,
    h11: Input<number>, h12: Input<number>, h13: Input<number>,
    h21: Input<number>, h22: Input<number>, h23: Input<number>,
    h31: Input<number>, h32: Input<number>, h33: Input<number>,
    context: Context?,
    instance: Artboard<nil>?,
    anim: Animation?,
    canvas: Canvas?,
    sampler: ImageSampler?,
    vertices: VertexBuffer?,
    uvs: VertexBuffer?,
    triangles: TriangleBuffer?,
}

local N = 16

local function init(self: PlaneArtboard, context: Context): boolean
    self.context = context
    local inst = self.artboard:instance()
    inst.frameOrigin = false
    self.instance = inst
    self.anim = inst:animation(self.animation)
    local res = math.max(0.25, self.resolution)
    self.canvas = context:canvas({ width = math.max(1, math.ceil(self.width * res)),
        height = math.max(1, math.ceil(self.height * res)), clearColor = Color.rgba(0, 0, 0, 0) })
    self.sampler = ImageSampler('clamp', 'clamp', 'bilinear')
    local uvs = VertexBuffer()
    local tris = TriangleBuffer()
    for j = 0, N do
        for i = 0, N do
            uvs:add(Vector.xy(i / N, j / N))
        end
    end
    for j = 0, N - 1 do
        for i = 0, N - 1 do
            local a = j * (N + 1) + i
            tris:add(a, a + 1, a + N + 1)
            tris:add(a + 1, a + N + 2, a + N + 1)
        end
    end
    self.uvs = uvs
    self.triangles = tris
    self.vertices = VertexBuffer()
    return true
end

local function advance(self: PlaneArtboard, seconds: number): boolean
    return true
end

local function draw(self: PlaneArtboard, renderer: Renderer)
    local inst, canvas, vb, uvs, tris, sampler = self.instance, self.canvas, self.vertices, self.uvs, self.triangles, self.sampler
    if not (inst and canvas and vb and uvs and tris and sampler) then
        return
    end
    -- impose AE's time, then update the instance (its artboard has no default state machine that would replay
    -- the animation from 0: ae2rml gives one to the main artboard only)
    local a = self.anim
    if a then
        a:setTime(self.time)
    end
    inst:advance(0)
    local cv = canvas :: Canvas
    local res = math.max(0.25, self.resolution)
    local r = cv:beginFrame({ clearColor = Color.rgba(0, 0, 0, 0) })
    r:save()
    if res ~= 1 then
        r:transform(Mat2D.withScale(res, res))
    end
    inst:draw(r)
    r:restore()
    cv:endFrame()
    local v = vb :: VertexBuffer
    v:reset()
    for j = 0, N do
        for i = 0, N do
            local x, y = self.width * i / N, self.height * j / N
            local w = self.h31 * x + self.h32 * y + self.h33
            if math.abs(w) < 1e-9 then
                w = 1e-9
            end
            v:add(Vector.xy((self.h11 * x + self.h12 * y + self.h13) / w, (self.h21 * x + self.h22 * y + self.h23) / w))
        end
    end
    renderer:drawImageMesh(cv.image, sampler :: ImageSampler, v, uvs :: VertexBuffer, tris :: TriangleBuffer,
        self.blend :: BlendMode, 1)
end

return function(): Node<PlaneArtboard>
    return {
        artboard = late(), animation = '', time = 0, width = 100, height = 100, resolution = 1, blend = 'srcOver',
        h11 = 1, h12 = 0, h13 = 0, h21 = 0, h22 = 1, h23 = 0, h31 = 0, h32 = 0, h33 = 1,
        context = nil, instance = nil, anim = nil, canvas = nil, sampler = nil, vertices = nil, uvs = nil,
        triangles = nil, init = init, advance = advance, draw = draw,
    }
end
