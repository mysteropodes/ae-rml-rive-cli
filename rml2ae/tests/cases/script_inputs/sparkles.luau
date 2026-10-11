--!strict
-- Placeholder drawing script for the rml2ae script-input test (only its inputs matter there).
type Sparkles = { count: Input<number>, spin: Input<boolean>, tint: Input<Color> }

function init(self: Sparkles): boolean
    return true
end

function draw(self: Sparkles, renderer: Renderer) end

return function(): Node<Sparkles>
    return { init = init, draw = draw, count = 12, spin = true, tint = Color.rgb(255, 200, 0) }
end
