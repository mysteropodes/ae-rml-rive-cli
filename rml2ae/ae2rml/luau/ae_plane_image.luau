-- ae2rml: an image (a still, or the current frame of a video / sequence) on an After Effects 3D plane or Corner Pin,
-- in exact perspective. The image rectangle (0,0)-(width,height) in layer units is cut into a grid whose vertices go
-- through the keyed homography h11…h33 (layer space -> layer space, i.e. F⁻¹·H with F the 2D transform Rive already
-- applies); a fine grid keeps the per-triangle affine texture mapping close to the true perspective one.

type PlaneImage = {
    image: Input<string>,     -- ImageAsset name (keyed per frame for a video / sequence)
    width: Input<number>,     -- the footage size in layer units
    height: Input<number>,
    blend: Input<string>,     -- the layer's blend mode (Rive name)
    h11: Input<number>, h12: Input<number>, h13: Input<number>,
    h21: Input<number>, h22: Input<number>, h23: Input<number>,
    h31: Input<number>, h32: Input<number>, h33: Input<number>,
    context: Context?,
    images: { [string]: Image },
    sampler: ImageSampler?,
    vertices: VertexBuffer?,
    uvs: VertexBuffer?,
    triangles: TriangleBuffer?,
}

local N = 16

local function init(self: PlaneImage, context: Context): boolean
    self.context = context
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

local function imageOf(self: PlaneImage, name: string): Image?
    local img = self.images[name]
    if img == nil and self.context then
        local found = (self.context :: Context):image(name)
        if found then
            self.images[name] = found
            img = found
        end
    end
    return img
end

local function draw(self: PlaneImage, renderer: Renderer)
    local img = imageOf(self, self.image)
    local vb, uvs, tris, sampler = self.vertices, self.uvs, self.triangles, self.sampler
    if not (img and vb and uvs and tris and sampler) then
        return
    end
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
    renderer:drawImageMesh(img :: Image, sampler :: ImageSampler, v, uvs :: VertexBuffer, tris :: TriangleBuffer,
        self.blend :: BlendMode, 1)
end

return function(): Node<PlaneImage>
    return {
        image = '', width = 100, height = 100, blend = 'srcOver',
        h11 = 1, h12 = 0, h13 = 0, h21 = 0, h22 = 1, h23 = 0, h31 = 0, h32 = 0, h33 = 1,
        context = nil, images = {}, sampler = nil, vertices = nil, uvs = nil, triangles = nil,
        init = init, draw = draw,
    }
end
