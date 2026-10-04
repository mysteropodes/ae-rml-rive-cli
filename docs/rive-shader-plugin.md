# Rive Shader: After Effects plugin

Rive Shader is an After Effects effect (SmartFX, under **Rive > Rive Shader**, match name `RIVE RiveShader`) that
runs a Rive `.wgsl` shader on a layer, unchanged. The layer is texture 0, the shader's `struct Params` becomes
effect parameters, and extra textures become Layer parameters. Rendering uses wgpu-native (Metal) linked
statically: upload, one full-screen pass, read back, at 8, 16 and 32 bits per channel.

It exists so that a post-process written for Rive (and the [fxlib](fxlib.md) effects) can be seen in After Effects,
and so that [rml2ae](../README.md) can reproduce Rive-only content such as image meshes.

Platform: macOS, Apple Silicon, After Effects 2024 or later (developed and tested on 26.x).

## Build and install

Requirements:

- Xcode (the build uses Xcode's SDK; the newer SDK shipped with the Command Line Tools alone breaks linking);
- CMake and Ninja;
- the free **Adobe After Effects SDK**, downloaded from the Adobe developer console and unzipped where the build
  expects it (`~/.cache/ae-plugin-deps/AfterEffectsSDK`).

wgpu-native is downloaded by the build (`~/.cache/ae-plugin-deps/wgpu-native`, macOS arm64 release).

```bash
cd rml2ae/plugin
./build.sh              # CMake + Ninja, PiPL (Rez), ad hoc signature, output in ~/AE-Dev-Plugins/RiveShader.plugin
./build.sh clean        # full rebuild
./build.sh dist         # also makes a zip (set SIGN_IDENTITY to sign with your own identity)
./build.sh install      # also copies to the After Effects Plug-ins folder (After Effects must be closed)
```

`./build.sh install` is the way to get a new build loaded: After Effects loads the installed copy
`Plug-Ins/RiveShader.plugin`, and a linked development folder is not picked up once an installed copy exists.
Restart After Effects after installing.

**Unsigned plugin.** The build is signed ad hoc, not with an Apple Developer ID. The first time After Effects loads
it, macOS blocks it: open **System Settings > Privacy & Security** and allow it once, then relaunch After Effects.

## Using it

1. Apply **Rive > Rive Shader** to a layer.
2. Click **Load .wgsl...** and pick a shader file. The file is registered and the **Shader** slider is set to its
   id. The shader is recompiled whenever the file's modification time changes, so you can edit it and re-render.
3. Adjust the generated parameters.

The shader is chosen by the integer **Shader** slider (scriptable). The id is resolved through
`~/Library/Application Support/RiveShader/shaders.tsv`, lines of `id<TAB>path`.

### WGSL conventions

Same as a Rive `post.wgsl`:

| Binding | Content |
|---|---|
| `@group(0) @binding(0)` | Source texture: the layer, **premultiplied** |
| `@group(0) @binding(1)` | Linear / clamp sampler |
| `@group(0) @binding(2)` | `var<uniform> P: Params` |
| `@group(0) @binding(3+)` | Extra textures, exposed as Texture 1..3 (layer pickers; an empty one is a 1x1 transparent texture) |

Entry points: `vs_main` (full-screen triangle) and `fs_main`.

`struct Params` may contain `f32`, `i32`, `u32`, `vec2`, `vec3` and `vec4` fields with the WGSL uniform layout.
Reserved fields filled by the host:

| Field | Value |
|---|---|
| `size` | Layer size in pixels |
| `tick` | The "Step fps" parameter (0 = the frame index) |
| `fxTick` | The "FX fps" parameter (0 = `tick`) |
| `seed` | The "Seed" parameter |
| `pad*` | Padding |

All other fields are turned into parameters, in declaration order:

- `vec2` becomes **Pt1..Pt4** (a point, in layer pixels);
- `vec3` / `vec4` whose comment contains "color" become **C1..C4**;
- everything else becomes sliders **P1..P16**, one scalar per slider.

A trailing comment such as `// 0..1 default 0.06` sets the slider's range and the value applied when you switch to
that shader.

### Parameter index

For ExtendScript (`effect.property(i)`):

| Index | Parameter |
|---|---|
| 1 | Shader |
| 2 | Load .wgsl... (button) |
| 3 | Step fps |
| 4 | FX fps |
| 5 | Seed |
| 6-21 | P1..P16 |
| 22-25 | C1..C4 |
| 26-29 | Pt1..Pt4 |
| 30-32 | Texture 1..3 |

### Errors

An unknown shader id, an unreadable file, invalid WGSL or a missing GPU never opens a dialog. The input is passed
through unchanged, the name of the **Shader** parameter says what failed, and the detail is written to
`~/Library/Application Support/RiveShader/riveshader.log`.

## Behaviour inside an effect stack

The shader **always works on the layer rectangle**, like a Rive canvas equals its artboard: `uv` runs 0..1 over the
layer, `size` and the Pt points are in layer pixels, whatever the effects above it did. Effects above that grow the
buffer (Drop Shadow, Glow, blurs) or shrink it (a mask) therefore do not shift, stretch or crop the shader's
result. Anything that falls outside the layer rectangle (for example the halo of an effect above) is passed
through unshaded so the next effect still receives it. Effects below the shader are unaffected.

Checked in After Effects 26.5: a test disc stays exactly in place and matches the reference to 0/255 when the
shader sits under Drop Shadow, a blur or Glow, above Drop Shadow or a blur, and with a mask.

## Rive image meshes

Rive can draw an image through a mesh (vertices, UV coordinates, triangles, optionally skinned to bones). After
Effects has no native equivalent: Mesh Warp is a grid and Puppet triangulates automatically.

rml2ae converts a Rive image mesh into:

1. a **pre-comp** holding the image;
2. the **Rive Shader** effect on it, running a **generated WGSL** that draws the mesh triangles exactly: a
   per-triangle affine mapping from the image to its position, in painter's order;
3. the vertex positions of every frame, stored in a **data image** with 16 bits per coordinate and plugged into the
   effect's **Texture 1**. The shader reads the current frame's vertices from that texture.

The result matches Rive's render (the triangles are rasterised, not approximated), and the animation is driven by
the data image rather than by keyframes you can edit.

**Without the plugin**, rml2ae falls back to **row strips driven by Corner Pin**: the image is cut into strips of
the mesh and each is pinned. This is approximate (the strips do not follow curved deformation within a row) and is
not meant to be edited by hand.

## Tests

`rml2ae/plugin/test/`:

| Script | Checks | Result |
|---|---|---|
| `oracle.py` | GPU module alone vs `rml2ae/wgsl_apply.py` | 8 bit identical to the bit, float within 1/255; about 3 ms per 1080p frame; flat memory over 6,000 renders |
| `copytest.py` | Copy-through at 8/16/32 bpc, grow bounds, mask, offset | 0/255 |
| `shadertest.py` | A shader in After Effects vs the oracle, tick, unknown id, broken shader | 0/255 (8 bit), within 1 (16 bit), pass-through on errors |
| `maptest.py` | Layer textures, colour, point, sliders | 0/255 |
| `stacktest.py` | Placement of the shader's output inside effect stacks and masks | 8 of 8 cases, 0/255 |

The harnesses refuse to run unless the open After Effects project is empty (or contains only their own items) and
capture at most 10 frames per script. Long renders go through `aerender` on a saved copy.

## Out of scope

Multi-pass chains orchestrated by Luau (use one effect instance per pass), compute shaders, storage buffers,
vertex buffers, `@group` above 0, state kept between frames, After Effects' native GPU render path, multi-threaded
rendering (the GPU module is serialised by a mutex), and Developer ID signing / notarisation.
