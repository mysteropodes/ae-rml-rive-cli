# fxlib: After Effects effects as WGSL

`rml2ae/ae2rml/fxlib/` is a library of native After Effects raster effects re-implemented as WGSL shaders for Rive.
Each effect is a shader plus a manifest. The same description serves three tools:

- **ae2rml** turns an After Effects effect with no Rive equivalent into a generated Luau node running the shader
  (see [ae2rml](ae2rml.md#effects));
- **rml2ae** turns that node back into the native After Effects effect, with its parameters, static or keyed;
- the [Rive Shader plugin](rive-shader-plugin.md) can run the same shaders inside After Effects.

Round trips After Effects to Rive to After Effects were measured at the pixel: 0.000 % of pixels differ on a
Tint then Invert stack, and at most 0.007 % on a Fill plus adjustment layer.

## Files

| File | Role |
|---|---|
| `ae_<slug>.wgsl` | The shader |
| `<slug>.json` | The manifest (below) |
| `_ae_params.json` | Parameter lists of each effect as measured in After Effects 2026: for each effect, `{i, mn, name, type, default}` per parameter |
| `_ae_fx_mix.wgsl` | Final mix pass used by adjustment layers (opacity and in/out window as an exact lerp) |

The parameter **position** `i` (ExtendScript `property(i)`) is what the manifests use. It is not always the number
in the parameter's match name: Fill's colour is `property(3)` but `ADBE Fill-0002`. `_ae_params.json` is the lookup.

## Shader contract

**Bindings** (Rive post-process convention), all in `@group(0)`:

| Binding | Name | Content |
|---|---|---|
| 0 | `srcTex` | The layer content, **premultiplied** RGBA |
| 1 | sampler | Linear sampler |
| 2 | `var<uniform> P: Params` | Parameters |
| 3+ | `origTex`, `mapTex` | Extra textures: `origTex` = the untouched original (multi-pass effects), `mapTex` = a second layer (for example Displacement Map) |

**Vertex stage**: a full-screen triangle (copy `vs_main` from an existing shader). **Output**: premultiplied RGBA.
Pixels outside the content are transparent: do not clamp-smear the edge unless the After Effects effect itself
repeats edge pixels.

**`struct Params`**:

- starts with `size: vec2<f32>` (canvas size in pixels);
- then the After Effects parameters, in **raw After Effects units**: degrees, pixels, 0..255 thresholds, 0..100
  percentages, 1-based menu indices, colours as straight (non-premultiplied) `vec4` 0..1, points in layer pixels.
  All conversion maths lives in the shader, so the manifest is only an index map;
- ends with `passIndex: f32` (the name `pass` is a WGSL keyword), plus padding;
- optionally followed by the reserved field `layerRect: vec4<f32>` = `(x0, y0, x1, y1)`, the layer's rectangle in the
  canvas. The canvas is the layer grown by `fxPad` on each side when an effect in the stack spills outside the
  layer (glow, shadow). Effects whose result depends on the layer rectangle (Linear Wipe, CC Scale Wipe) read it.

**Manifest** (`<slug>.json`):

```json
{
  "slug": "tint",
  "aeMatchName": "ADBE Tint",
  "wgsl": "ae_tint.wgsl",
  "passes": 1,
  "textures": {},
  "params": [
    { "ae": 1, "field": "mapBlack", "kind": "color",  "default": [0, 0, 0, 1] },
    { "ae": 2, "field": "mapWhite", "kind": "color",  "default": [1, 1, 1, 1] },
    { "ae": 3, "field": "amount",   "kind": "number", "default": 100 }
  ],
  "status": "exact",
  "notes": "...",
  "verified": { }
}
```

| Field | Meaning |
|---|---|
| `ae` | 1-based After Effects parameter index (`property(i)`) |
| `field` | Name of the field in `Params` |
| `kind` | `number`, `point`, `color`, `enum`, `bool` or `angle` |
| `textures` | Extra textures the effect needs, for example `{"origTex": "original", "mapTex": "map"}` |
| `textureParams` | For a `map` texture: the AE position of the layer parameter it comes from, e.g. `{"mapTex": 1}` (ae2rml binds that layer) |
| `passes` | Number of times the shader runs |
| `status` | `exact`, `close` or `approx` (below) |
| `auto` | `false` means ae2rml does **not** apply the effect automatically |
| `verified` | Measurements and notes from the independent check |

**Multi-pass**: the same shader runs `passes` times. Pass 0 reads the content; pass `i` reads the previous output as
`srcTex`; `P.passIndex = i`; `origTex` always holds the untouched content.

### WGSL pitfalls in Rive's parser

- `pass` is reserved (use `passIndex`).
- Uniformity analysis: use `textureSampleLevel` inside loops and branches.
- `]]` is rejected: write `let i = los[k]; A[r - i]`.
- `a < b.x && c >= 0` can be read as a generic type: parenthesise the comparison.
- Screenshots saved by After Effects' `saveFrameToPng` are premultiplied; account for it when comparing.

## Status levels

| Status | Meaning |
|---|---|
| exact | Within 1-2 levels (of 255) of After Effects on every checked setting |
| close | Small, localised differences (up to about 12 levels at a few points); the effect is faithful |
| approx | Visible differences on some settings |

## How effects are verified

1. Reference renders are made in After Effects 2026 (8 bpc) on test layers with the effect at several settings.
2. The shader is run offline with wgpu and compared to the render: mean and maximum error in levels, and the
   percentage of pixels off by more than 8 levels. With `--rive`, the Rive CLI's own render of the generated node is
   compared too.
3. **Held-out settings.** Each effect is also checked on at least one setting that was never shown to whoever wrote
   the shader. This catches formulas that were fitted to the visible settings only. When a held-out setting exposed
   a wrong rule, the rule was corrected on that setting; the manifest's `verified.note` then states that the
   check is no longer independent. This happened for two effects (Posterize used `/256` instead of `/255`; Color
   Balance (HLS) had the positive-saturation law wrong).
4. Real projects are used as a final test: an approximate effect that makes a real composition worse than leaving
   the effect out is marked `"auto": false`.

The references live in `rml2ae/ae2rml/fxref/`: `spec.py` lists the settings of each effect (`held = 1` for the
held-out ones) and writes `renders.json`; `render_refs.py` renders them in After Effects (macOS, After Effects open)
into `ae/` and `ae_holdout/` from the synthetic source `src/src.png` (640 x 360) and `src/map.png`.

Commands (from the repository root, with the project's Python environment):

```bash
python -m rml2ae.ae2rml.fxlib check <slug> [--rive] [--holdout] [--keep]   # references vs offline wgpu (and Rive CLI)
python -m rml2ae.ae2rml.fxlib luau <slug>[,<slug>...]                      # print the generated Rive node for a stack
python -m rml2ae.ae2rml.fxlib regress [<slug>...] [--update]               # every reference vs fxref/baseline.json
python rml2ae/ae2rml/fxref/spec.py && python rml2ae/ae2rml/fxref/render_refs.py <slug>   # new references (macOS + AE)
```

### Regression gate (CI)

`fxlib regress` measures every reference of every effect, held-out settings included, with wgpu on the CPU, and
compares each one with `fxref/baseline.json`. It fails when a setting gets further from After Effects than the
baseline (mean +0.02 %, pixels over 8 levels +0.05 %, or max +2 levels), when a shader stops compiling, when a
reference has no baseline yet, when a manifest names a `Params` field or an After Effects parameter that does not
exist, or when an unverified effect that reads `layerRect` gives a different result once the node has a pad (the
layer grown by transparent pixels, point parameters moved accordingly, as the Rive node does). The CI runs it on Linux with Mesa's software Vulkan (lavapipe) and `wgpu==0.32.0`, and prints the full table in
the job summary. After a deliberate change (a better shader, a new effect), run it with `--update` and commit the
new `baseline.json` with the change; the diff shows what moved.

The baseline is a lavapipe measurement, not the status of the effect: the `status` and `verified` fields of each
manifest come from the measurements made on macOS and stay the reference for "exact / close / approx".

### Reference renders (`rml2ae/ae2rml/fxref/`)

Everything needed to measure an effect is in the repository:

| Path | Content |
|---|---|
| `fxref/src/src.png`, `src_premult.png`, `map.png` | the synthetic 640×360 test card (gradients, shapes, checker, text, semi-transparent disc) and the displacement map |
| `fxref/spec.py` | every effect and its settings: `{AE parameter index: value}`, `held=1` for the held-out ones |
| `fxref/renders.json` | generated by `python spec.py`; what `fxlib check` reads |
| `fxref/ae/`, `fxref/ae_holdout/` | the After Effects 2026 renders (8 bpc, premultiplied PNG) of the visible and held-out settings |
| `fxref/render_refs.py` | renders missing references in After Effects (macOS, AppleScript) |

`fxlib check` reads `rml2ae/ae2rml/fxref/` (override with `AE2RML_FXREF=<folder>`) and writes its outputs to
`~/.cache/ae2rml/fxcheck` (override with `AE2RML_FXCHECK_OUT`), never into the repository.

### Adding an effect

1. Add the effect and its settings to `fxref/spec.py` (at least two visible settings and one with `held=1`), then
   `python spec.py`.
2. With After Effects open and **no other project with content open**, run
   `python rml2ae/ae2rml/fxref/render_refs.py <slug>`. It opens (or creates on first use) its own project
   `fxref/fxref.aep` (not committed), renders in batches of 8 frames and skips references that already exist
   (`--all` re-renders). Set `AE_APP` if your After Effects is not "Adobe After Effects 2026".
3. Write `fxlib/ae_<slug>.wgsl` and `fxlib/<slug>.json` looking only at the visible renders, iterate with
   `fxlib check <slug> --rive`.
4. Have someone else (or a later session) run `fxlib check <slug> --rive --holdout` and record the result in the
   manifest's `verified` field. Contributing new references: commit the PNGs of `fxref/ae*/` with the spec change.

## Effect table

What is still missing, effect by effect, is in [fxlib-roadmap.md](fxlib-roadmap.md).

37 measured effects: 27 exact, 8 close, 2 approx, plus 117 unverified ones (below the table). "Auto" is whether ae2rml applies the effect without being asked; the two effects
marked `no` are measured to be further from After Effects than leaving the effect out, so they remain a
`<!-- ae: effect ... -->` comment plus an entry in `effects_todo.json`.

| Effect | Match name | Slug | Passes | Status | Auto | Note |
|---|---|---|---|---|---|---|
| Black & White | `ADBE Black&White` | `black_white` | 1 | close | yes | Exact without the Tint option; Tint option within +-1 level in clipped shadows/highlights |
| Box Blur | `ADBE Box Blur2` | `box_blur` | 2 | exact | yes |  |
| Brightness & Contrast | `ADBE Brightness & Contrast 2` | `brightness_contrast` | 1 | exact | yes |  |
| CC Radial Blur | `CC Radial Blur` | `cc_radial_blur` | 1 | approx | no | Straight Zoom, Centered Zoom and Scratch measured (0.1-0.4 % mean); Fading Zoom, Rotate, Rotate Fading inferred |
| CC Radial Fast Blur | `CC Radial Fast Blur` | `cc_radial_fast_blur` | 64 | close | yes | Within 12 levels |
| CC Scale Wipe | `CC Scale Wipe` | `cc_scale_wipe` | 1 | exact | yes |  |
| Checkerboard | `ADBE Checkerboard` | `checkerboard` | 1 | exact | yes |  |
| Color Balance (HLS) | `ADBE Color Balance (HLS)` | `color_balance_hls` | 1 | exact | yes |  |
| Corner Pin | `ADBE Corner Pin` | `corner_pin` | 1 | exact | yes |  |
| Directional Blur | `ADBE Motion Blur` | `directional_blur` | 1 | exact | yes |  |
| Displacement Map | `ADBE Displacement Map` | `displacement_map` | 1 | close | yes |  |
| Drop Shadow | `ADBE Drop Shadow` | `drop_shadow` | 2 | exact | yes |  |
| Emboss | `ADBE Emboss` | `emboss` | 1 | exact | yes |  |
| Exposure | `ADBE Exposure2` | `exposure` | 1 | exact | yes |  |
| Fast Blur (legacy) | `ADBE Fast Blur` | `fast_blur_legacy` | 2 | exact | yes |  |
| Fill | `ADBE Fill` | `fill` | 1 | exact | yes |  |
| 4-Color Gradient | `ADBE 4ColorGradient` | `four_color_gradient` | 1 | exact | yes |  |
| Gaussian Blur | `ADBE Gaussian Blur 2` | `gaussian_blur` | 2 | exact | yes |  |
| Gaussian Blur (legacy) | `ADBE Gaussian Blur` | `gaussian_blur_legacy` | 2 | exact | yes |  |
| Glow | `ADBE Glo2` | `glow` | 2 | close | yes | Max error 7-11 levels at a few interior spots |
| Gradient Ramp | `ADBE Ramp` | `gradient_ramp` | 1 | exact | yes |  |
| Hue/Saturation | `ADBE HUE SATURATION` | `hue_saturation` | 1 | exact | yes |  |
| Invert | `ADBE Invert` | `invert` | 1 | exact | yes |  |
| Levels | `ADBE Easy Levels2` | `levels` | 1 | exact | yes |  |
| Linear Wipe | `ADBE Linear Wipe` | `linear_wipe` | 3 | exact | yes |  |
| Luma Key | `ADBE Luma Key` | `luma_key` | 4 | close | yes | Max 7 levels on the feathered matte edge |
| Magnify | `ADBE Magnify` | `magnify` | 1 | close | yes |  |
| Minimax | `ADBE Minimax` | `minimax` | 4 | exact | yes |  |
| Mosaic | `ADBE Mosaic` | `mosaic` | 4 | exact | yes | Fractional tiles are area-sampled |
| Motion Tile | `ADBE Tile` | `motion_tile` | 1 | exact | yes |  |
| Optics Compensation | `ADBE Optics Compensation` | `optics_compensation` | 1 | close | yes | After Effects softens edges at large FOV; not reproduced |
| Posterize | `ADBE Posterize` | `posterize` | 1 | exact | yes |  |
| Shift Channels | `ADBE Shift Channels` | `shift_channels` | 1 | exact | yes |  |
| Simple Choker | `ADBE Simple Choker` | `simple_choker` | 2 | close | yes | Max 5 levels at chokes 3 and -4; the held-out choke 6 is off by up to 119 levels on 0.75 % of pixels (the law changes with the choke: renders of chokes 1, 10, 20, -10 requested in `fxref/spec.py`) |
| Tint | `ADBE Tint` | `tint` | 1 | exact | yes |  |
| Tritone | `ADBE Tritone` | `tritone` | 1 | exact | yes |  |
| Turbulent Displace | `ADBE Turbulent Displace` | `turbulent_displace` | 1 | approx | no | After Effects noise is proprietary: same scale and strength, different pattern |

**Unverified effects.** Written from each effect's definition and checked to compile and run, but not yet measured
against After Effects renders: `"status": "unverified"`, `"auto": false` (ae2rml leaves them out unless asked). Their
visible settings are in `fxref/spec.py`; render them on a Mac (`render_refs.py --params <slug>` first, to record the
real parameter list, then `render_refs.py <slug>`), add held-out settings, and measure.

| Effect | Match name | Slug | Open questions to measure |
|---|---|---|---|
| Channel Mixer | `ADBE Channel Mixer` | `channel_mixer` | Parameter order; rounding of the constants |
| Set Channels | `ADBE Set Channels` | `set_channels` | Only the layer itself as source; Luminance weights, HLS of greys |
| Offset | `ADBE Offset` | `offset` | Sub-pixel filtering of fractional shifts |
| Radial Wipe | `ADBE Radial Wipe` | `radial_wipe` | Feather law (distance to the edge ray here) |
| Venetian Blinds | `ADBE Venetian Blinds` | `venetian_blinds` | Stripe origin and direction convention, feather law |
| Photo Filter | `ADBE Photo Filter` | `photo_filter` | Menu index of Custom; preset colours; luminosity law |
| Vibrance | `ADBE Vibrance` | `vibrance` | Approximation: Adobe's vibrance law is undocumented |
| Threshold | `ADBE Threshold2` | `threshold` | Luma weights, >= vs > |
| Gamma/Pedestal/Gain | `ADBE Gamma/Pedestal/Gain` | `gamma_pedestal_gain` | Black Stretch law; order of gamma and pedestal/gain |
| Leave Color | `ADBE Leave Color` | `leave_color` | Distance in RGB and in hue; softness ramp |
| Sharpen | `ADBE Sharpen` | `sharpen` | Kernel and strength per Amount |
| Find Edges | `ADBE Find Edges` | `find_edges` | Operator and scale |
| Unsharp Mask | `ADBE Unsharp Mask2` | `unsharp_mask` | Radius to sigma; threshold per channel or luma |
| Mirror | `ADBE Mirror` | `mirror` | Which side is kept at a given angle |
| Polar Coordinates | `ADBE Polar Coordinates` | `polar_coordinates` | Radius normalisation, angle origin |
| Twirl | `ADBE Twirl` | `twirl` | Radius reference and falloff |
| Bulge | `ADBE Bulge` | `bulge` | Height law, taper, pinning |
| Spherize | `ADBE Spherize` | `spherize` | Sphere law |
| Radial Blur | `ADBE Radial Blur` | `radial_blur` | Amount units per type; sampling |
| CC Vignette | `CC Vignette` | `cc_vignette` | Falloff law and Angle of View |
| Iris Wipe | `ADBE Iris Wipe` | `iris_wipe` | Polygon when Use Inner Radius is off; feather law |
| Color Balance | `ADBE Color Balance 2` | `color_balance` | Tonal weights; Preserve Luminosity law |
| Change Color | `ADBE Change Color` | `change_color` | Distance per Match Colors mode; transforms in HSL |
| CC Toner | `CC Toner` | `cc_toner` | Gradient stops per Tones mode |
| CC Color Offset | `CC Color Offset` | `cc_color_offset` | Overflow modes |
| Arithmetic | `ADBE Arithmetic` | `arithmetic` | Operator menu order; rounding |
| Remove Color Matting | `ADBE Remove Color Matting` | `remove_color_matting` | Clipping |
| Color Key | `ADBE Color Key` | `color_key` | Distance; Edge Thin and Edge Feather |
| Extract | `ADBE Extract` | `extract` | Softness ramps; luma weights |
| Spill Suppressor | `ADBE Spill Suppressor` | `spill_suppressor` | Suppression law |
| CC Threshold | `CC Threshold` | `cc_threshold` | Channel menu; >= vs > |
| CC Threshold RGB | `CC Threshold RGB` | `cc_threshold_rgb` | Parameter order |
| Color Emboss | `ADBE Color Emboss` | `color_emboss` | Built on the measured Emboss; base colour assumed |
| Median | `ADBE Median` | `median` | Window shape (square here); radius cap 12 |
| Wave Warp | `ADBE Wave Warp` | `wave_warp` | Displacement axis; Wave Speed (time) not modelled |
| Ripple | `ADBE Ripple` | `ripple` | Radius reference, falloff, Type |
| CC Tiler | `CC Tiler` | `cc_tiler` | Tile origin |
| Grid | `ADBE Grid` | `grid` | Parameter positions after Border; Feather |
| Ellipse | `ADBE Ellipse` | `ellipse` | Ring geometry and softness |
| Bevel Alpha | `ADBE Bevel Alpha` | `bevel_alpha` | Height field and shading law |
| Radial Shadow | `ADBE Radial Shadow` | `radial_shadow` | Projection law; Softness |
| Channel Blur | `ADBE Channel Blur` | `channel_blur` | Assumed legacy blur law (Gaussian of equal variance) |
| CC Cross Blur | `CC Cross Blur` | `cc_cross_blur` | Kernel shape; Transfer Mode |
| Bilateral Blur | `ADBE Bilateral Blur` | `bilateral_blur` | Spatial and range weights; Colorize |
| Gradient Wipe | `ADBE Gradient Wipe` | `gradient_wipe` | The layer itself as gradient only; softness law |
| Block Dissolve | `ADBE Block Dissolve` | `block_dissolve` | Random pattern cannot match AE; feather |
| Circle | `ADBE Circle` | `circle` | Parameter positions after Radius; Edge and Feather |
| CC Light Rays | `CC Light Rays` | `cc_light_rays` | Brightness weighting of the rays; Shape |
| CC Light Burst 2.5 | `CC Light Burst 2.5` | `cc_light_burst` | Burst modes; Halo Alpha |
| CC Spotlight | `CC Spotlight` | `cc_spotlight` | Cone geometry from Height and Cone Angle |
| Noise | `ADBE Noise` | `noise` | Random pattern cannot match AE; amount law |
| Scatter | `ADBE Scatter` | `scatter` | Random pattern cannot match AE |
| Broadcast Colors | `ADBE Broadcast Colors` | `broadcast_colors` | Signal amplitude formula per locale |
| CC Power Pin | `CC Power Pin` | `cc_power_pin` | The measured Corner Pin law; Perspective < 100 % and Expansion |
| CC Radial ScaleWipe | `CC Radial ScaleWipe` | `cc_radial_scalewipe` | Push-out law; Reverse |
| CC Slant | `CC Slant` | `cc_slant` | Shear origin; Stretching |
| CC Split | `CC Split` | `cc_split` | Slit taper |
| CC Lens | `CC Lens` | `cc_lens` | Lens law and size reference |
| CC Kaleida | `CC Kaleida` | `cc_kaleida` | Wedge count; Mirroring modes |
| Solid Composite | `ADBE Solid Composite` | `solid_composite` | Blending modes |
| Dust & Scratches | `ADBE Dust & Scratches` | `dust_scratches` | Window and threshold test |
| Noise Alpha | `ADBE Noise Alpha` | `noise_alpha` | Random pattern cannot match AE; modes |
| Noise HLS | `ADBE Noise HLS2` | `noise_hls` | Random pattern cannot match AE; amounts |
| CC Light Sweep | `CC Light Sweep` | `cc_light_sweep` | Band profiles; Edge Intensity |
| Beam | `ADBE Laser` | `beam` | Length/Time window; 3D Perspective |
| CC Jaws | `CC Jaws` | `cc_jaws` | Tooth shapes; travel distance |
| CC Line Sweep | `CC Line Sweep` | `cc_line_sweep` | Staggering of the lines |
| CC Light Wipe | `CC Light Wipe` | `cc_light_wipe` | Glow profile; shapes |
| Bevel Edges | `ADBE Bevel Edges` | `bevel_edges` | Thickness reference; shading |
| Linear Color Key | `ADBE Linear Color Key2` | `linear_color_key` | Parameter positions; distance |
| Smart Blur | `ADBE Smart Blur` | `smart_blur` | Threshold test; edge modes |
| Reduce Interlace Flicker | `ADBE Reduce Interlace Flicker` | `reduce_interlace_flicker` | Kernel |
| Advanced Spill Suppressor | `ADBE Spill2` | `advanced_spill_suppressor` | Standard method only |
| Levels (Individual Controls) | `ADBE Pro Levels2` | `levels_individual` | Parameter indices of the channel groups; order of channel and master levels |
| Change to Color | `ADBE Change To Color` | `change_to_color` | Tolerance and softness law in HLS; Change modes |
| Channel Combiner | `ADBE Channel Combiner` | `channel_combiner` | From / To menu order; YUV constants; the layer itself as source only |
| Color Range | `ADBE Color Range` | `color_range` | Lab scaling; Fuzziness law; parameter positions |
| Matte Choker | `ADBE Matte Choker` | `matte_choker` | Softness to sigma; choke threshold law; Iterations |
| CC Kernel | `CC Kernel` | `cc_kernel` | Parameter list (Divider, Absolute Value); edge handling |
| CC Vector Blur | `CC Vector Blur` | `cc_vector_blur` | Direction per Type; length law; the layer itself as vector map only |
| CC Bend It | `CC Bend It` | `cc_bend_it` | Bend units; what happens past End; Distort modes |
| CC Griddler | `CC Griddler` | `cc_griddler` | Tile size reference; overlaps without Cut Tiles |
| CC Simple Wire Removal | `CC Simple Wire Removal` | `cc_simple_wire_removal` | Removal styles; Slope law; Frame Offset (time) not modelled |
| Lens Flare | `ADBE Lens Flare` | `lens_flare` | A stylised flare: AE's elements are not reproduced |
| CC Grid Wipe | `CC Grid Wipe` | `cc_grid_wipe` | Order of the cells; shapes; Border |
| CC Block Load | `CC Block Load` | `cc_block_load` | Block levels and fill order; Scanlines |
| Blend | `ADBE Blend` | `blend` | Color Only / Tint Only laws; Blend With Original direction |
| Calculations | `ADBE Calculations` | `calculations` | Blending Mode menu order; alpha when not preserving transparency |
| Compound Arithmetic | `ADBE Compound Arithmetic` | `compound_arithmetic` | Operator menu; Scale overflow ranges |
| Difference Matte | `ADBE Difference Matte2` | `difference_matte` | Distance measure; Blur Before Difference not modelled |
| Texturize | `ADBE Texturize` | `texturize` | Relief and lighting law; Texture Contrast scale |
| Compound Blur | `ADBE Compound Blur` | `compound_blur` | Kernel shape (disc here) and the radius law |
| CC Image Wipe | `CC Image Wipe` | `cc_image_wipe` | Parameter positions in the Gradient group; Property menu; Blur |
| 3D Glasses | `ADBE 3D Glasses2` | `three_d_glasses` | 3D View menu order; Balance; the left view is this layer |
| CC Bender | `CC Bender` | `cc_bender` | Bend profile per Style; Adjust To Distance |
| CC Split 2 | `CC Split 2` | `cc_split2` | How the two splits blend along the slit |
| CC Smear | `CC Smear` | `cc_smear` | Falloff and Reach law |
| CC Ripple Pulse | `CC Ripple Pulse` | `cc_ripple_pulse` | Ring radius per Pulse Level, wavelength; Time Span |
| CC Flo Motion | `CC Flo Motion` | `cc_flo_motion` | Pull law and Falloff |
| CC Twister | `CC Twister` | `cc_twister` | Twist distribution along the axis; Backside layer |
| CC Page Turn | `CC Page Turn` | `cc_page_turn` | Curl geometry; Controls presets; Back Page layer |
| CC Sphere | `CC Sphere` | `cc_sphere` | Mapping orientation; light and shading parameter positions |
| CC Cylinder | `CC Cylinder` | `cc_cylinder` | Radius reference; Rotation X / Z; shading parameter positions |
| Warp | `ADBE WRPMESH` | `warp` | Match name; the 15 styles' envelopes; distortion law |
| Bezier Warp | `ADBE BEZMESH` | `bezier_warp` | Match name; patch form (Coons here) |
| Colorama | `APC Colorama` | `colorama` | Get Phase From menu; output presets (only the Hue Cycle here; the custom wheel is not read) |
| Selective Color | `ADBE Selective Color` | `selective_color` | Parameter positions of the 9 colour groups; class weights; relative law |
| Shadow/Highlight | `ADBE Shadow/Highlight` | `shadow_highlight` | Local luminance and tonal-width law; Auto Amounts |
| CC Color Neutralizer | `CC Color Neutralizer` | `cc_color_neutralizer` | Band weights around Pivot; Contrast |
| Color Difference Key | `ADBE Color Difference Key` | `color_difference_key` | Partial mattes A / B; parameter positions |
| Eyedropper Fill | `ADBE Sample Fill` | `eyedropper_fill` | Match name; Average Pixel Colors menu |
| Cartoon | `ADBE Cartoonify` | `cartoon` | Match name; smoothing, shading steps and edge laws |
| CC Glass | `CC Glass` | `cc_glass` | Parameter positions; refraction and shading laws; the layer itself as bump map |
| CC HexTile | `CC HexTile` | `cc_hextile` | What a tile shows; Smearing; Render |
| CC RepeTile | `CC RepeTile` | `cc_repetile` | Tiling menu; Blend Borders (the node grows by the largest expansion) |
| CC Burn Film | `CC Burn Film` | `cc_burn_film` | Random pattern cannot match AE; rim colours |
| Camera Lens Blur | `ADBE Camera Lens Blur` | `camera_lens_blur` | Iris shape menu; highlight law; parameter positions |

Notes:

- **Gaussian Blur (legacy)** and **Fast Blur (legacy)** share the same kernel (confirmed on a third setting).
- **Motion Tile** is the effect named `ADBE Tile`.
- **Drop Shadow**, **Glow**, **Linear Wipe** and the multi-pass blurs use several passes; for the stack as a whole
  ae2rml runs every pass in one node.
- **CC Radial Blur**: types 1, 3, 4 and 6 are measured. Centered Zoom (3) was first inferred and wrong; it was refitted
  on its held-out reference (uniform scales [1 - Amount/400, 1 + Amount/400], inner end sampled, outer end not).
- **Mosaic**: tiles cut at fractional positions (layer size not a multiple of the block count) are area-sampled: a
  pixel on a boundary mixes the two tiles by coverage. Found and fixed on the held-out 13 x 7 reference.
- **Turbulent Displace**: After Effects' noise function is proprietary. Scale, strength and pinning match; the noise
  pattern differs. Measured on real compositions, the missing effect is closer to After Effects than the approximate one.

## Using the library

- **From ae2rml**: automatic. An effect whose match name is in the library (and not `auto: false`) becomes a
  generated node `fx_<slug>[__<slug>...].luau` covering the layer's whole stack.
- **From rml2ae**: a node named `fx_...` becomes a pre-comp of its source plus the native effects in order, with
  static or keyed parameters. `fxMix` becomes Effect Opacity (Compositing Options). A group's mix goes on each of its
  effects' Effect Opacity; the blend mode of an adjustment layer is reported (it needs an adjustment layer in After
  Effects).
- **Adding an effect**: write `ae_<slug>.wgsl`, start a manifest from a similar effect, add its settings to
  `fxref/spec.py` (include at least one held-out setting chosen by someone else), render the references with
  `fxref/render_refs.py`, run `fxlib check <slug> --rive --holdout`, record `status` and `verified` in the manifest,
  then `fxlib regress <slug> --update`. Until it is measured, a new effect is `"status": "unverified"` with
  `"auto": false`.
