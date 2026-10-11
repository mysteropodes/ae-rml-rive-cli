# ae2rml: After Effects project to Rive CLI project

`ae2rml` reads an After Effects project (`.aep`) and writes a Rive CLI project: `rive.yaml`, `scene.rml` and
`assets/`. After Effects does not need to be running. The binary `.aep` is parsed with
[py-aep](https://github.com/forticheprod/py-aep) (MIT, by Fortiche Prod), to which this tool owes its ability to
read projects without the application. The RML is produced directly; there is no Lottie intermediate step.

Contents: [Usage](#usage) · [Outputs](#outputs) · [What converts](#what-converts) · [Effects](#effects) ·
[Expressions](#expressions) · [Round trip with After Effects](#round-trip-with-after-effects) ·
[Known limits](#known-limits)

## Usage

```bash
.venv/bin/python -m rml2ae.ae2rml project.aep out_dir [options]
ae import project.aep out_dir [options]          # same thing through the `ae` command
```

Try it on the bundled example:

```bash
.venv/bin/python -m rml2ae.ae2rml examples/demo.aep out/demo --verify --shot 1 3.5
rive out/demo                                    # open the result in the Rive CLI viewer
```

If `out_dir` is omitted, it defaults to `<project name>_rive` next to the current directory.

### Options

| Option | Effect |
|---|---|
| `--comp NAME` | Main composition to convert. Default: the largest root composition (one that is not used inside another). Compositions reachable from it are converted too. |
| `--fps N` | Override the frame rate. Default: the composition's own rate. |
| `--once` | Animations play once (`oneShot`). Default: they loop. |
| `--no-bg` | Do not draw the composition background colour. |
| `--verify` | After writing, run `rive <out_dir> --verify` and report success or the last lines of its output. |
| `--shot T [T ...]` | After writing, save a Rive screenshot at each time (seconds, or `500ms`) to `out_dir/build/shot_<T>.png`. Implies `--verify`. |
| `--media-scale S` | Scale video frames, image-sequence frames and still images by `S` (default 1) to keep the `.riv` light. Opaque images are written as JPEG, images with transparency as PNG. |
| `--media-fps N` | At most `N` distinct images per second of footage (default: one per composition frame). |
| `--no-audio` | No sound: neither audio layers nor the sound of videos. |
| `--no-video` | Leave video files (`.mov`, `.mp4`, ...) out; their layers draw nothing. Image sequences and stills are kept. |
| `--ae-lang fr\|en\|any` | UI language of After Effects to emulate when resolving names in expressions (see [Expressions](#expressions)). Default: the `AE2RML_LANG` environment variable, else the system language. |

`--shot` times account for a capture offset of the Rive CLI: a screenshot taken with `--advance=T` shows the
animation at `T - 1/60 s`, so the tool captures at `T + 1/60 s`. When comparing with an After Effects render at
time `T`, use the same convention.

## Outputs

Besides the project itself, `out_dir/build/ae2rml/` contains:

| File | Content |
|---|---|
| `report.md` | What was converted, approximated or not converted, plus fonts and footage found or missing. Read this first. |
| `effects_todo.json` | Effects with no Rive equivalent and no [fxlib](fxlib.md) implementation, with their parameters: the work list for writing WGSL shaders. |
| `expressions_failed.json` | Expressions the offline interpreter could not evaluate. |
| `idmap.json` | Stable object ids, so that re-importing the same project gives a readable diff and stable tags. |
| `tag_ae_project.jsx` | Script that tags the original After Effects project for [`ae pull`](#round-trip-with-after-effects). |
| `bake_expressions.jsx` | Present only when some expressions need After Effects to be evaluated (see [Expressions](#expressions)). |

The console summary gives artboard count, the main composition, and counts of converted, approximate and
unsupported items.

## What converts

Legend: **exact** = reproduced to anti-aliasing noise; **approx** = reproduced with a known difference, noted in
`report.md`; **no** = not converted.

### Structure and transforms

| After Effects | Rive | Fidelity |
|---|---|---|
| Composition (main + reachable pre-compositions) | One artboard per composition (collapsed pre-comps are not clipped), a `LinearAnimation` at the composition frame rate, and an entry state machine | exact |
| Pre-comp layer | `NestedArtboard` with a keyed remap time (start time, stretch and time remap supported) | exact |
| Layer transform | `Node` for position/rotation/scale plus a second `Node` for the anchor; opacity and in/out window on a contained node (hold keys) | exact |
| Parenting | Rive hierarchy. When children are not contiguous in the After Effects stack, the parent is repeated (a ghost node with the same keys) so drawing order stays identical | exact |
| Keyframes | Hold, linear and bezier (influence + speed) become exact cubic ease interpolators; curved spatial paths, keys before time 0 and negative stretch are sampled then reduced. Keys that fall between frames are moved to neighbouring frames with the curve cut exactly | exact |
| Blend modes | Rive names; modes without an equivalent map to the closest of the same family and are reported | approx |
| Solo | A soloed layer only hides the others when it has an image; soloed audio or guide layers leave everything visible, as in After Effects | exact |
| Lights (Classic 3D: parallel, spot, point, ambient) | Shading baked into the colours of each 3D layer that accepts lights, keyed per frame when it changes: ambient + Lambert diffuse + Blinn-Phong highlight (Metal mixes its colour), spot cone and feather, the three falloff modes. The model matches After Effects 2026 renders within 1.5 levels per pixel; Rive gets one value per layer, taken at its anchor, so a spot or point light does not vary across the layer. No shadows; a still image takes the shading of the first frame | approx |
| Layer styles on vector layers (shape, text, solid): Drop Shadow, Outer Glow, Inner Shadow, Inner Glow, Color Overlay, Gradient Overlay, Stroke | Copies of the layer's shapes above or below it, in Photoshop's stacking order: feathered copies for shadows and glows (Spread as a stroke, inner ones with Rive's inner `Feather`), a recoloured copy for overlays (gradient across the layer's bounds), fills turned into a `Stroke` at the style's position (inside, centre, outside). Animated style parameters use their first value; noise, choke and Fill Opacity are not reproduced. See [Layer styles](#layer-styles) | approx |
| Bevel and Emboss, Satin, Pattern Overlay; any style on footage or a pre-comp | Reported | no |

### Shapes

| After Effects | Rive | Fidelity |
|---|---|---|
| Rectangle, ellipse, path, star/polygon | `Rectangle`, `Ellipse`, `PointsPath` with keyed vertices | exact |
| Fill, stroke, gradients (including animated gradient colours) | Paints (order reversed inside a group, as Rive expects) | exact |
| Dashes | `DashPath`, or a generated Luau path effect when preceded by a trim or path effect | exact |
| Trim Paths | `TrimPath` inside the paint (applies to stroke and fill) | exact |
| Round Corners | Vertex `radius` | exact |
| Repeater | Real copies (compounded transforms, start/end opacity) | exact |
| Merge Paths | Union, subtract (clockwise fill + hole), exclude (even-odd), intersect (clipping shapes). Paints placed above a Merge Paths are ignored, as After Effects does | exact |
| Group skew | Exact rotation-scale-rotation on two nodes | exact |
| Taper | Outline converted to a fill with a Luau taper script | exact |
| Zig Zag, Offset Paths, Pucker & Bloat, Twist | Luau `ScriptedPathEffect` placed in each paint, parameters keyed as script inputs | exact |
| Offset Paths of ±half the stroke width right before a stroke (closed paths, one copy) | `Stroke` with `position` inside or outside on the original paths, no Luau (the sign follows the path's winding: a positive amount moves a clockwise path outwards) | exact |
| Wiggle Paths | Same structure; the random draws differ (After Effects' generator is not public) | approx |

Paint semantics follow After Effects: a paint draws every path above it in its group, including paths of
sub-groups through their (animated) transforms, and a path operation applies to all paths above it.

### Text

| After Effects | Rive | Fidelity |
|---|---|---|
| Point and paragraph text | `Text` + `TextStylePaint`; font looked up by PostScript name on the Mac (Adobe Fonts included), otherwise Helvetica (After Effects' own fallback) and a report entry | exact when the font is found |
| Several character styles in one text | One style and run per range | exact |
| Keyed source text | `KeyFrameString` | exact |
| Animators (position, scale, rotation, opacity) with a range selector | `TextModifierGroup` + `TextModifierRange` | exact |
| Expression selector | One range per text unit, strength keyed frame by frame from the evaluated expression | exact |
| Wiggly selector, random order, smooth up/down | Strengths baked per character | approx |
| Text on a path | `TextFollowPathModifier` on an unpainted shape | exact |
| Animated tracking, per-character colour and offset | Per-character runs / translations | exact |
| Per-character 3D (animators with Z position, X / Y rotation, Z anchor) | One `TextModifierGroup` per character, keyed per frame: the 2D affine closest to how the camera sees that character's own plane (position, turn, foreshortening). The perspective inside one glyph and the shear of combined X / Y turns are lost; paragraph text keeps the one-plane projection | approx |

### Masks, mattes, blending

| After Effects | Rive | Fidelity |
|---|---|---|
| Masks (add, subtract, intersect, lighten, darken, difference), keyed paths | `ClippingShape` (even-odd tricks for subtract/difference) | exact |
| Keyed mask opacity | Path collapses to a point where opacity is under 50 % | approx |
| Open masks | Ignored (After Effects only cuts with closed masks) | exact |
| Track mattes (alpha, luma, inverted, text, image, gradient, pre-comp, with or without the matte's eye on) | `LayerMask` on the matted layer's node (`maskModeValue` 0-3 = alpha, inverted alpha, luma, inverted luma): the matte layer is drawn as it is (paints, opacity, in/out, effects) in a « · matte source » node that Rive hides (`sourceDraws` false) unless its eye is on | exact (unverified against AE) |
| Track mattes with `--clip-mattes` (runtimes older than `LayerMask`) | Clip by the matte's geometry; a matte stroke is converted to an outline path first; luma treated as alpha; text or image mattes not converted | exact / approx |
| Stencil / silhouette blend modes | The layer's geometry cuts every layer below, including below a collapsed pre-comp | exact |
| Set Matte effect | Clip in composition space from the source layer | exact |

### Footage, audio, 3D

| After Effects | Rive | Fidelity |
|---|---|---|
| Solid | `Rectangle` | exact |
| Still image | `ImageAsset` (PNG, or JPEG if opaque) | exact |
| Layered PSD | Each layer imported separately by a built-in PSD reader (8 and 16 bit, RLE/ZIP); a PSD without layers is flattened | exact |
| `.ai`, `.eps`, `.pdf` | Rasterised with `sips` | approx |
| Image sequence | Only displayed frames are extracted; a `Solo` whose active child is keyed frame by frame | exact |
| Video | Exported as an image sequence (only shown frames); start time, stretch, remap and loops honoured; colour effects baked per displayed frame. Use `--media-scale` / `--media-fps` to limit size | exact (heavy) |
| Audio | `AudioAsset` + `AudioEvent` at the entry point (WAV, MP3, FLAC; other formats converted to WAV with ffmpeg), trimmed to the part After Effects plays; the sound of videos is extracted separately | exact |
| 3D layers and cameras | Projection by the active camera (default camera, one-node, two-node, parented). Shapes, solids, text: exact (Luau projection). Images, videos, sequences: exact (16x16 mesh). Pre-comps: rendered flat to a canvas then mapped. Masks, 3D track mattes, "orient towards camera", depth ordering (keyed draw rules when it changes) | exact / approx |
| Depth of field | A `Feather` on the vector paints of each 3D layer, keyed from its distance to the focus: σ = 0.04 × the thin lens' circle of confusion (aperture × \|depth − focus\| / depth × zoom / focus × blur level), measured on After Effects 2026 renders (Classic 3D blurs little: 1-2 px for a layer 500 px off focus). One blur per layer, at its anchor; footage and pre-comps are not blurred | approx |
| 3D approximations | Stroke width is not foreshortened; no 3D inside a collapsed pre-comp; renderers other than Classic 3D are drawn with its rules | approx |
| Corner Pin on an ordinary layer | Same homography | exact |
| Transform adjustment layer | Animated nodes around all layers below, identity outside its duration | exact |

### Essential properties

An instance of a pre-comp whose Essential Graphics properties are overridden (colour, source text, opacity,
trim, ...; static or keyed) becomes a **variant** of its artboard built with those values, one per distinct set of
overrides. Overrides aimed at deeper pre-comps travel with them, including into pre-comps that read the value
through `comp("...")`.

## Effects

| After Effects effect | What ae2rml writes |
|---|---|
| Transform effect | Exact nodes |
| Fill, Tint, Invert, Exposure, Levels, Color Balance (HLS), Brightness & Contrast, Hue/Saturation | Applied to colours directly (exact on vector content; baked into PNGs for images). On a pre-comp layer, applied inside a variant of its artboard |
| Gradient Ramp | A real Rive gradient (points in composition coordinates, keyed if the layer moves) |
| Gaussian Blur, Fast Blur, Box Blur on shape/text layers | `Feather` on each paint, keyed if animated (about 0.02-0.16 % of pixels differ). With "Repeat Edge Pixels" on (After Effects' default), the layer outline stays sharp, so no feather is applied and a note is written |
| Drop Shadow on shape/text layers | Copy of the drawing below the layer with shadow-coloured, feathered paints |
| Glow on shape/text layers | Feathered copy on top in screen blend mode (Rive has no additive mode) |
| Curves | Applied to colours directly from the curves AE stores (py-aep decodes its 256-level maps): the master curve, then each channel's. Keyed curves hold from key to key. The alpha curve is not applied |
| Stroke (along masks) | A Rive `Stroke` on the mask path(s) (open masks included) with a `TrimPath` for Start / End (keyed), Brush Hardness as a feather, "On Transparent" hides the layer. Spacing and "Reveal Original Image" are not reproduced. Not measured against After Effects yet |
| Posterize Time | All keys of the layer held at its rate |
| Any other raster effect found in the [WGSL effect library](fxlib.md) | One generated Luau node running the WGSL passes for the layer's **whole effect stack**, in After Effects' order |
| Effects that read another layer (Displacement Map) | The other layer's source is bound to the node: a precomp (its artboard), a still image, or the layer itself; other layer kinds leave the map empty (reported) |
| Anything else | Kept as an `<!-- ae: effect ... -->` comment (so rml2ae can put it back) and listed in `effects_todo.json` |
| Adjustment layers | Library effects: the layers below go into a sub-artboard and the adjustment layer's opacity and in/out window drive a mix pass. Several stacked adjustment layers give one node with one group per layer, and the layer's blend mode is applied by the rule measured in After Effects. Otherwise listed in `effects_todo.json` |

### Layer styles

Each enabled style is a copy of the layer's drawables with its paints replaced, placed in the layer's node (top to
bottom: Stroke, Inner Shadow, Inner Glow, Color Overlay, Gradient Overlay, the layer, Outer Glow, Drop Shadow). The
light angle follows Photoshop (Use Global Light reads the layer's Global Light angle): 120° casts the shadow down and
to the right. Style blend modes map to Rive's; Linear Dodge becomes `additive`.

Not yet measured against After Effects: the factor from a style's Size to Rive's Feather strength
(`STYLE_SIZE_K` = 0.66, Size taken as about 3 σ) and Rive's inner feather against Photoshop's inner shadow. Each
converted style is listed as approximate in `report.md`.

Notes on the WGSL path:

- Nested effect nodes render nothing in Rive (measured), so every pass of a stack runs in one node, alternating
  between three GPU canvases.
- The content goes into a sub-artboard (`FxArtboard`); its keys leave the composition's animation.
- A colour effect on **pixels** (footage, or a pre-comp containing footage) also goes through WGSL, because
  recolouring vector paints would leave the image untouched.
- An effect node drawn inside the canvas of another script renders nothing in Rive. ae2rml disables the inner node
  (content passes through) and reports it; the outer effect works.
- Adjustment layers inside a collapsed pre-comp apply to the layers below the pre-comp in the parent composition.
- Effects marked "not applied automatically" in the [library table](fxlib.md#effect-table) are left as comments.

## Expressions

Expressions are evaluated **outside** After Effects by a built-in JavaScript interpreter (`aexpr.py`) that
provides the After Effects globals (`thisComp`, `layer()`, `effect()()`, `content()`, `valueAtTime`, `loopOut`,
`linear`/`ease`, `sourceRectAtTime`, `toComp`, `path.pointOnPath`, ...). A time-independent result becomes a static
value; otherwise it is sampled and fitted with curves.

- A property read inside its own expression returns its pre-expression value, as in After Effects.
- An expression that fails returns the pre-expression value to whoever reads it (After Effects disables it) and is
  logged in `expressions_failed.json`.
- Names are resolved in the emulated After Effects language: a French After Effects only resolves
  `effect("X")("Curseur")`, not `("Slider")`, and otherwise disables the expression. ae2rml does the same
  (`--ae-lang`).
- **`bake_expressions.jsx`**: run it inside After Effects (it is read-only). It samples, frame by frame, the values
  of expressions that need the application: compiled JSXBIN code, missing APIs, and expressions that depend on
  `random()`, `gaussRandom()`, `wiggle()` or `noise()` (After Effects' generator is not public). It writes
  `bake.json`, which the next conversion uses.

## Round trip with After Effects

Run `build/ae2rml/tag_ae_project.jsx` once in the **original** After Effects project (File > Scripts). It writes
`rive:<id>` in each layer's comment and `rive:<artboard>|<animation>` in each composition's comment, only where the
comment is empty or already a tag. Afterwards:

- `ae pull out_dir` reads the edits made in After Effects (transforms, keys, eases) back into `scene.rml`;
- re-importing the `.aep` keeps the same ids (`idmap.json`), so diffs stay readable.

## Known limits

- **Lights, depth of field, per-character 3D**: approximated (see the tables above): one shading and one blur per
  layer, no shadows, no perspective inside a single glyph.
- **Layer styles**: approximate on vector layers (see [Layer styles](#layer-styles)); Bevel and Emboss, Satin, Pattern
  Overlay and styles on footage or pre-comps are reported, not converted.
- **Effects without a library implementation**: kept as comments; see `effects_todo.json`.
- **Random generators** (wiggle, noise, random order, Wiggly selector, Wiggle Paths): structure is reproduced, the
  random draws differ.
- **Fonts**: a font not installed is replaced by Helvetica and reported; glyph overlap is better than with Arial
  but not identical.
- **Missing media**: After Effects draws colour bars for missing footage; ae2rml draws nothing. Differences in
  frames with missing media are expected.
- **Raster effects on Rive's side**: a blur on shape or text layers is rendered with `Feather`; it is exact to a
  fraction of a percent of pixels but not identical to a raster blur.
- **Large projects**: reading is fast (projects from 1 to 5,000 layers load in 0.02-4 s), but video-heavy
  projects produce very large `.riv` files; use `--media-scale`, `--media-fps` and `--no-video`.
- **Old project formats**: projects saved by After Effects CS6 (11.0.1) may be unreadable by py-aep. Open and save
  a copy with a recent After Effects; the copy converts normally.
- **py-aep quirks handled by ae2rml**: fractional key times, mask coordinates on shape/text layers, effect
  parameters not stored in the instance (defaults are read from the effect definition as After Effects does),
  null-layer opacity, localised property names, and a Transform-effect anchor/position sometimes read 100 times
  too large (values beyond 10 times the layer size are divided by 100 and the report says so).
- When After Effects opens a project it may show two dialogs that scripts cannot suppress (version conversion,
  missing media); click OK.

## Accuracy checks

Conversions were checked against After Effects 2026 renders: roughly 0.0-0.9 % of pixels differ on test
compositions built by script (anti-aliasing), and real-world projects reach 0.97-1.0 intersection-over-union on
drawn areas. The remaining larger differences come from missing media, absent fonts and unimplemented effects.
Keyframe interpolation was compared frame by frame with py-aep's own interpolation on more than 1,500 animated
tracks (median error 0). 3D projection agrees with py-aep's After Effects-verified camera oracles to 1e-12 px.
