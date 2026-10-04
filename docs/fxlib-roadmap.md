# fxlib roadmap: every native After Effects effect

Goal: every native After Effects effect that makes sense on a single frame gets a WGSL version in
[fxlib](fxlib.md), measured against After Effects renders. This page is the inventory, grouped as in After Effects'
Effects menu (English UI names).

| Mark | Meaning |
|---|---|
| ✅ | In fxlib and measured (`exact`, `close` or `approx`, see the effect table in [fxlib.md](fxlib.md)), or converted natively by ae2rml |
| 🟡 | In fxlib, **unverified**: written from the effect's definition, compiles and runs, waits for After Effects renders |
| ⬜ | To do |
| ⛔ | Not planned, with the reason |

Typical reasons for ⛔: the effect depends on other frames (time effects, motion estimation), on image statistics
gathered over the whole clip, on audio, on masks or paths drawn on the layer, on 3D geometry, or it is a third-party
plug-in. Some can come later through the Rive scene itself rather than as a shader.

## Blur & Sharpen

| Effect | | Note |
|---|---|---|
| Bilateral Blur | 🟡 | lot 4 |
| Box Blur (Fast Box Blur) | ✅ | |
| Camera Lens Blur | 🟡 | lot 11 (no blur map layer) |
| Camera-Shake Deblur | ⛔ | motion estimation over several frames |
| CC Cross Blur | 🟡 | lot 4 |
| CC Radial Blur | ✅ | |
| CC Radial Fast Blur | ✅ | |
| CC Vector Blur | 🟡 | lot 8; the layer itself as vector map |
| Channel Blur | 🟡 | lot 4 |
| Compound Blur | 🟡 | lot 9 (second layer) |
| Directional Blur | ✅ | |
| Fast Blur (legacy) | ✅ | |
| Gaussian Blur | ✅ | |
| Gaussian Blur (legacy) | ✅ | |
| Radial Blur | 🟡 | lot 2 |
| Reduce Interlace Flicker | 🟡 | lot 7 |
| Sharpen | 🟡 | lot 2 |
| Smart Blur | 🟡 | lot 7 |
| Unsharp Mask | 🟡 | lot 2 |

## Channel

| Effect | | Note |
|---|---|---|
| Arithmetic | 🟡 | lot 3 |
| Blend | 🟡 | lot 9 (second layer) |
| Calculations | 🟡 | lot 9 (second layer) |
| CC Composite | ⬜ | parameter list already measured |
| Channel Combiner | 🟡 | lot 8; the layer itself as source |
| Compound Arithmetic | 🟡 | lot 9 (second layer) |
| Invert | ✅ | |
| Minimax | ✅ | |
| Remove Color Matting | 🟡 | lot 3 |
| Set Channels | 🟡 | lot 1; the layer itself as source |
| Set Matte | ✅ | converted natively by ae2rml (clip from the source layer), no shader needed |
| Shift Channels | ✅ | |
| Solid Composite | 🟡 | lot 6 |

## Color Correction

| Effect | | Note |
|---|---|---|
| Auto Color, Auto Contrast, Auto Levels | ⛔ | statistics of the frame and its neighbours |
| Black & White | ✅ | |
| Brightness & Contrast | ✅ | |
| Broadcast Colors | 🟡 | lot 5 |
| CC Color Neutralizer | 🟡 | lot 11 |
| CC Color Offset | 🟡 | lot 3 |
| CC Kernel | 🟡 | lot 8 |
| CC Toner | 🟡 | lot 3 |
| Change Color | 🟡 | lot 3 |
| Change to Color | 🟡 | lot 8 |
| Channel Mixer | 🟡 | lot 1 |
| Color Balance | 🟡 | lot 3 |
| Color Balance (HLS) | ✅ | |
| Color Link | ⛔ | samples another layer over time |
| Color Stabilizer | ⛔ | tracks reference frames |
| Colorama | 🟡 | lot 11 (Hue Cycle output only; the custom wheel is custom data) |
| Curves | ⬜ | the curve is custom data: needs a reader for it |
| Equalize | ⛔ | histogram of the frame |
| Exposure | ✅ | |
| Gamma/Pedestal/Gain | 🟡 | lot 2 |
| Hue/Saturation | ✅ | |
| Leave Color | 🟡 | lot 2 |
| Levels | ✅ | |
| Levels (Individual Controls) | 🟡 | lot 8 (the measured Levels law per channel) |
| Lumetri Color | ⛔ | too large for now (LUTs, curves, wheels); later |
| Photo Filter | 🟡 | lot 1 |
| PS Arbitrary Map | ⬜ | |
| Selective Color | 🟡 | lot 11 |
| Shadow/Highlight | 🟡 | lot 11 |
| Tint | ✅ | |
| Tritone | ✅ | |
| Vibrance | 🟡 | lot 1 |

## Distort

| Effect | | Note |
|---|---|---|
| Bezier Warp | 🟡 | lot 10 |
| Bulge | 🟡 | lot 2 |
| CC Bend It | 🟡 | lot 8 |
| CC Bender | 🟡 | lot 10 |
| CC Blobbylize | 🟡 | lot 12 (the layer itself as blob layer) |
| CC Flo Motion | 🟡 | lot 10 |
| CC Griddler | 🟡 | lot 8 |
| CC Lens | 🟡 | lot 6 |
| CC Page Turn | 🟡 | lot 10 (the back shows the layer itself) |
| CC Power Pin | 🟡 | lot 6 (the measured Corner Pin law) |
| CC Ripple Pulse | 🟡 | lot 10 (Pulse Level only, not Time Span) |
| CC Slant | 🟡 | lot 6 |
| CC Smear | 🟡 | lot 10 |
| CC Split | 🟡 | lot 6 |
| CC Split 2 | 🟡 | lot 10 |
| CC Tiler | 🟡 | lot 4 |
| Corner Pin | ✅ | |
| Detail-preserving Upscale | ⛔ | learned upscaler |
| Displacement Map | ✅ | |
| Liquify | ⛔ | brush strokes stored in the effect |
| Magnify | ✅ | |
| Mesh Warp | ⬜ | |
| Mirror | 🟡 | lot 2 |
| Offset | 🟡 | lot 1 |
| Optics Compensation | ✅ | |
| Polar Coordinates | 🟡 | lot 2 |
| Reshape | ⛔ | mask-driven |
| Ripple | 🟡 | lot 4 |
| Rolling Shutter Repair | ⛔ | motion estimation |
| Smear | ⛔ | mask-driven |
| Spherize | 🟡 | lot 2 |
| Transform | ✅ | converted natively by ae2rml (exact transform nodes), no shader needed |
| Turbulent Displace | ✅ | approx: proprietary noise |
| Twirl | 🟡 | lot 2 |
| Warp | 🟡 | lot 10 (approximate envelopes) |
| Warp Stabilizer | ⛔ | motion estimation |
| Wave Warp | 🟡 | lot 4 |

## Generate

| Effect | | Note |
|---|---|---|
| 4-Color Gradient | ✅ | |
| Advanced Lightning | 🟡 | lot 12 (one bolt; random, differs from AE) |
| Audio Spectrum, Audio Waveform | ⛔ | audio |
| Beam | 🟡 | lot 7 |
| CC Glue Gun | ⬜ | |
| CC Light Burst 2.5 | 🟡 | lot 5 |
| CC Light Rays | 🟡 | lot 5 |
| CC Light Sweep | 🟡 | lot 7 |
| CC Threads | 🟡 | lot 12 |
| Cell Pattern | 🟡 | lot 12 (random draw differs from AE) |
| Checkerboard | ✅ | |
| Circle | 🟡 | lot 5 |
| Ellipse | 🟡 | lot 4 |
| Eyedropper Fill | 🟡 | lot 11 |
| Fill | ✅ | |
| Fractal | 🟡 | lot 12 |
| Gradient Ramp | ✅ | |
| Grid | 🟡 | lot 4 |
| Lens Flare | 🟡 | lot 8 (stylised: AE's flare elements are not reproduced) |
| Paint Bucket | ⬜ | flood fill |
| Radio Waves | ⛔ | particles over time |
| Scribble, Stroke, Vegas, Write-on | ⛔ | mask- or path-driven |

## Keying

| Effect | | Note |
|---|---|---|
| Advanced Spill Suppressor | 🟡 | lot 7 |
| CC Simple Wire Removal | 🟡 | lot 8 (Frame Offset needs other frames) |
| Color Difference Key | 🟡 | lot 11 |
| Color Key | 🟡 | lot 3 |
| Color Range | 🟡 | lot 8 |
| Difference Matte | 🟡 | lot 9 (second layer) |
| Extract | 🟡 | lot 3 |
| Inner/Outer Key | ⛔ | mask-driven |
| Key Cleaner | ⬜ | |
| Linear Color Key | 🟡 | lot 7 |
| Luma Key | ✅ | |
| Spill Suppressor | 🟡 | lot 3 |

## Matte

| Effect | | Note |
|---|---|---|
| Matte Choker | 🟡 | lot 8 |
| Refine Hard Matte, Refine Soft Matte | ⛔ | motion-aware |
| Simple Choker | ✅ | close; more renders requested |

## Noise & Grain

| Effect | | Note |
|---|---|---|
| Add Grain, Match Grain, Remove Grain | ⛔ | grain models and sampling over time |
| Dust & Scratches | 🟡 | lot 6 |
| Fractal Noise, Turbulent Noise | 🟡 | lot 12 (proprietary noise: same parameters, different pattern) |
| Median | 🟡 | lot 3 |
| Noise | 🟡 | lot 5 (random pattern differs from AE) |
| Noise Alpha, Noise HLS | 🟡 | lot 6 (random pattern differs from AE) |
| Noise HLS Auto | 🟡 | lot 12 (static: no time in the node) |

## Perspective

| Effect | | Note |
|---|---|---|
| 3D Glasses | 🟡 | lot 9 (right view = second layer, left = this layer) |
| Bevel Alpha | 🟡 | lot 4 |
| Bevel Edges | 🟡 | lot 7 |
| CC Cylinder, CC Sphere | 🟡 | lot 10 (Lambert shading only) |
| CC Spotlight | 🟡 | lot 5 |
| Drop Shadow | ✅ | |
| Radial Shadow | 🟡 | lot 4 |

## Stylize

| Effect | | Note |
|---|---|---|
| Brush Strokes | 🟡 | lot 12 (random strokes differ from AE) |
| Cartoon | 🟡 | lot 11 |
| CC Block Load | 🟡 | lot 8 |
| CC Burn Film | 🟡 | lot 11 (random pattern differs from AE) |
| CC Glass | 🟡 | lot 11 (the layer itself as bump map) |
| CC HexTile | 🟡 | lot 11 |
| CC Kaleida | 🟡 | lot 6 |
| CC Mr. Smoothie | ⬜ | |
| CC Plastic | 🟡 | lot 12 |
| CC RepeTile | 🟡 | lot 11 |
| CC Threshold, CC Threshold RGB | 🟡 | lot 3 |
| CC Vignette | 🟡 | lot 2 |
| Color Emboss | 🟡 | lot 3 |
| Emboss | ✅ | |
| Find Edges | 🟡 | lot 2 |
| Glow | ✅ | |
| Mosaic | ✅ | |
| Motion Tile | ✅ | |
| Posterize | ✅ | |
| Roughen Edges | 🟡 | lot 12 (proprietary noise) |
| Scatter | 🟡 | lot 5 (random pattern differs from AE) |
| Strobe Light | ⛔ | depends on time |
| Texturize | 🟡 | lot 9 (second layer) |
| Threshold | 🟡 | lot 2 |

## Transition

| Effect | | Note |
|---|---|---|
| Block Dissolve | 🟡 | lot 5 (random pattern differs from AE) |
| Card Wipe | ⛔ | 3D cards |
| CC Image Wipe | 🟡 | lot 9 (second layer) |
| CC Glass Wipe | 🟡 | lot 12 (reveal = second layer; gradient = this layer) |
| CC Grid Wipe | 🟡 | lot 8 |
| CC Jaws | 🟡 | lot 7 |
| CC Light Wipe | 🟡 | lot 7 |
| CC Line Sweep | 🟡 | lot 7 |
| CC Radial ScaleWipe | 🟡 | lot 6 |
| CC Scale Wipe | ✅ | |
| CC Twister | 🟡 | lot 10 |
| CC WarpoMatic | ⬜ | |
| Gradient Wipe | 🟡 | lot 5 (the layer itself as gradient) |
| Iris Wipe | 🟡 | lot 2 |
| Linear Wipe | ✅ | |
| Radial Wipe | 🟡 | lot 1 |
| Venetian Blinds | 🟡 | lot 1 |

## Not covered by this list

Time effects (Echo, Posterize Time, Time Difference, Time Displacement, Timewarp, Pixel Motion Blur, CC Force
Motion Blur, CC Wide Time), 3D effects (CC Particle World, Shatter, Caustics, Wave World, Foam, Card Dance), text and
expression controls, audio effects and the Immersive Video effects are outside fxlib: they need several frames, 3D
geometry or audio, or are not image effects.

## Effects that read a second layer

Blend, Calculations, Compound Arithmetic, Compound Blur, Difference Matte, 3D Glasses, Texturize, CC Glass Wipe,
CC Image Wipe and Gradient Wipe with another layer read the shader's `mapTex` from a layer parameter (manifest
`textureParams.mapTex` = its AE position). ae2rml binds it to the Rive node's `e<i>_mapSource` input: a precomp
gives its comp artboard, a still image a sub-artboard with the image, the layer itself (AE's default) the effect's
own source artboard; other layer kinds (shapes, text, solids, video) leave the map empty and are reported.
`rml2ae/tests/test_map_layer.py` checks the binding, and `examples/make_fx_map_aep.jsx` builds a test project (to
make in After Effects) with the three kinds of map layer. Displacement Map (measured) and lot 9 (Blend, Calculations,
Compound Arithmetic, Compound Blur, Difference Matte, 3D Glasses, Texturize, CC Image Wipe) use it. In the node the
second layer is rendered at the layer's size, so AE's "If Layer Sizes Differ" / "Stretch to Fit" options have nothing
left to choose. CC Glass Wipe (lot 12) binds the revealed layer, its gradient is the layer itself. Still to do: Gradient Wipe
with another layer (today it uses the layer itself).

## How a batch goes

1. Write the shaders and manifests (`"status": "unverified"`, `"auto": false`) and add their visible settings to
   `rml2ae/ae2rml/fxref/spec.py`. The CI checks that they compile and run.
2. On a Mac with After Effects: `python render_refs.py --params <slugs>` (records the real parameter lists), then
   `python render_refs.py <slugs>` (renders the references). Commit `fxlib/_ae_params.json` and the PNGs.
3. Measure (`fxlib check <slug> --holdout`), fix the shaders, add held-out settings chosen by someone who has not read
   the shader, set `status` and `verified`, then `fxlib regress <slug> --update`.
