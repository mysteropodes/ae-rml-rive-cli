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
reference has no baseline yet, or when a manifest names a `Params` field or an After Effects parameter that does not
exist. The CI runs it on Linux with Mesa's software Vulkan (lavapipe) and `wgpu==0.32.0`, and prints the full table in
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

37 effects: 26 exact, 8 close, 3 approx. "Auto" is whether ae2rml applies the effect without being asked; the two effects
marked `no` are measured to be further from After Effects than leaving the effect out, so they remain a
`<!-- ae: effect ... -->` comment plus an entry in `effects_todo.json`.

| Effect | Match name | Slug | Passes | Status | Auto | Note |
|---|---|---|---|---|---|---|
| Black & White | `ADBE Black&White` | `black_white` | 1 | close | yes | Exact without the Tint option; Tint option within +-1 level in clipped shadows/highlights |
| Box Blur | `ADBE Box Blur2` | `box_blur` | 2 | exact | yes |  |
| Brightness & Contrast | `ADBE Brightness & Contrast 2` | `brightness_contrast` | 1 | exact | yes |  |
| CC Radial Blur | `CC Radial Blur` | `cc_radial_blur` | 1 | approx | no | Centered Zoom type was inferred and is wrong (23 % of pixels off by more than 8 levels) |
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
| Mosaic | `ADBE Mosaic` | `mosaic` | 4 | approx | yes | Non-integer tile sizes differ |
| Motion Tile | `ADBE Tile` | `motion_tile` | 1 | exact | yes |  |
| Optics Compensation | `ADBE Optics Compensation` | `optics_compensation` | 1 | close | yes | After Effects softens edges at large FOV; not reproduced |
| Posterize | `ADBE Posterize` | `posterize` | 1 | exact | yes |  |
| Shift Channels | `ADBE Shift Channels` | `shift_channels` | 1 | exact | yes |  |
| Simple Choker | `ADBE Simple Choker` | `simple_choker` | 2 | close | yes | Max 5 levels |
| Tint | `ADBE Tint` | `tint` | 1 | exact | yes |  |
| Tritone | `ADBE Tritone` | `tritone` | 1 | exact | yes |  |
| Turbulent Displace | `ADBE Turbulent Displace` | `turbulent_displace` | 1 | approx | no | After Effects noise is proprietary: same scale and strength, different pattern |
Notes:

- **Gaussian Blur (legacy)** and **Fast Blur (legacy)** share the same kernel (confirmed on a third setting).
- **Motion Tile** is the effect named `ADBE Tile`.
- **Drop Shadow**, **Glow**, **Linear Wipe** and the multi-pass blurs use several passes; for the stack as a whole
  ae2rml runs every pass in one node.
- **CC Radial Blur**: types 1, 4 and 6 were measured; the "Centered Zoom" type was inferred without a visible
  reference and is wrong.
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
