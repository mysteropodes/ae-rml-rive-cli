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
| Camera Lens Blur | ⬜ | iris shapes; depth map layer optional |
| Camera-Shake Deblur | ⛔ | motion estimation over several frames |
| CC Cross Blur | 🟡 | lot 4 |
| CC Radial Blur | ✅ | |
| CC Radial Fast Blur | ✅ | |
| CC Vector Blur | ⬜ | |
| Channel Blur | 🟡 | lot 4 |
| Compound Blur | ⬜ | needs a blur layer |
| Directional Blur | ✅ | |
| Fast Blur (legacy) | ✅ | |
| Gaussian Blur | ✅ | |
| Gaussian Blur (legacy) | ✅ | |
| Radial Blur | 🟡 | lot 2 |
| Reduce Interlace Flicker | ⬜ | |
| Sharpen | 🟡 | lot 2 |
| Smart Blur | ⬜ | |
| Unsharp Mask | 🟡 | lot 2 |

## Channel

| Effect | | Note |
|---|---|---|
| Arithmetic | 🟡 | lot 3 |
| Blend | ⬜ | needs a second layer |
| Calculations | ⬜ | needs a second layer |
| CC Composite | ⬜ | parameter list already measured |
| Channel Combiner | ⬜ | |
| Compound Arithmetic | ⬜ | needs a second layer |
| Invert | ✅ | |
| Minimax | ✅ | |
| Remove Color Matting | 🟡 | lot 3 |
| Set Channels | 🟡 | lot 1; the layer itself as source |
| Set Matte | ✅ | converted natively by ae2rml (clip from the source layer), no shader needed |
| Shift Channels | ✅ | |
| Solid Composite | ⬜ | |

## Color Correction

| Effect | | Note |
|---|---|---|
| Auto Color, Auto Contrast, Auto Levels | ⛔ | statistics of the frame and its neighbours |
| Black & White | ✅ | |
| Brightness & Contrast | ✅ | |
| Broadcast Colors | ⬜ | |
| CC Color Neutralizer | ⬜ | |
| CC Color Offset | 🟡 | lot 3 |
| CC Kernel | ⬜ | |
| CC Toner | 🟡 | lot 3 |
| Change Color | 🟡 | lot 3 |
| Change to Color | ⬜ | tolerance is a parameter group |
| Channel Mixer | 🟡 | lot 1 |
| Color Balance | 🟡 | lot 3 |
| Color Balance (HLS) | ✅ | |
| Color Link | ⛔ | samples another layer over time |
| Color Stabilizer | ⛔ | tracks reference frames |
| Colorama | ⬜ | parameter list already measured; large |
| Curves | ⬜ | the curve is custom data: needs a reader for it |
| Equalize | ⛔ | histogram of the frame |
| Exposure | ✅ | |
| Gamma/Pedestal/Gain | 🟡 | lot 2 |
| Hue/Saturation | ✅ | |
| Leave Color | 🟡 | lot 2 |
| Levels | ✅ | |
| Levels (Individual Controls) | ⬜ | same maths as Levels, one parameter per channel |
| Lumetri Color | ⛔ | too large for now (LUTs, curves, wheels); later |
| Photo Filter | 🟡 | lot 1 |
| PS Arbitrary Map | ⬜ | |
| Selective Color | ⬜ | |
| Shadow/Highlight | ⬜ | |
| Tint | ✅ | |
| Tritone | ✅ | |
| Vibrance | 🟡 | lot 1 |

## Distort

| Effect | | Note |
|---|---|---|
| Bezier Warp | ⬜ | |
| Bulge | 🟡 | lot 2 |
| CC Bend It, CC Bender | ⬜ | |
| CC Blobbylize | ⬜ | |
| CC Flo Motion | ⬜ | |
| CC Griddler | ⬜ | |
| CC Lens | ⬜ | |
| CC Page Turn | ⬜ | |
| CC Power Pin | ⬜ | close to Corner Pin |
| CC Ripple Pulse | ⬜ | |
| CC Slant | ⬜ | |
| CC Smear | ⬜ | |
| CC Split, CC Split 2 | ⬜ | |
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
| Warp | ⬜ | |
| Warp Stabilizer | ⛔ | motion estimation |
| Wave Warp | 🟡 | lot 4 |

## Generate

| Effect | | Note |
|---|---|---|
| 4-Color Gradient | ✅ | |
| Advanced Lightning | ⬜ | random; seed law to measure |
| Audio Spectrum, Audio Waveform | ⛔ | audio |
| Beam | ⬜ | |
| CC Glue Gun | ⬜ | |
| CC Light Burst 2.5 | ⬜ | |
| CC Light Rays | ⬜ | |
| CC Light Sweep | ⬜ | |
| CC Threads | ⬜ | |
| Cell Pattern | ⬜ | proprietary noise, like Fractal Noise |
| Checkerboard | ✅ | |
| Circle | ⬜ | |
| Ellipse | 🟡 | lot 4 |
| Eyedropper Fill | ⬜ | |
| Fill | ✅ | |
| Fractal | ⬜ | |
| Gradient Ramp | ✅ | |
| Grid | 🟡 | lot 4 |
| Lens Flare | ⬜ | |
| Paint Bucket | ⬜ | flood fill |
| Radio Waves | ⛔ | particles over time |
| Scribble, Stroke, Vegas, Write-on | ⛔ | mask- or path-driven |

## Keying

| Effect | | Note |
|---|---|---|
| Advanced Spill Suppressor | ⬜ | |
| CC Simple Wire Removal | ⬜ | |
| Color Difference Key | ⬜ | |
| Color Key | 🟡 | lot 3 |
| Color Range | ⬜ | |
| Difference Matte | ⬜ | needs a second layer |
| Extract | 🟡 | lot 3 |
| Inner/Outer Key | ⛔ | mask-driven |
| Key Cleaner | ⬜ | |
| Linear Color Key | ⬜ | |
| Luma Key | ✅ | |
| Spill Suppressor | 🟡 | lot 3 |

## Matte

| Effect | | Note |
|---|---|---|
| Matte Choker | ⬜ | |
| Refine Hard Matte, Refine Soft Matte | ⛔ | motion-aware |
| Simple Choker | ✅ | close; more renders requested |

## Noise & Grain

| Effect | | Note |
|---|---|---|
| Add Grain, Match Grain, Remove Grain | ⛔ | grain models and sampling over time |
| Dust & Scratches | ⬜ | |
| Fractal Noise, Turbulent Noise | ⬜ | proprietary noise: like Turbulent Displace, approx at best |
| Median | 🟡 | lot 3 |
| Noise, Noise Alpha, Noise HLS, Noise HLS Auto | ⬜ | random; seed law to measure |

## Perspective

| Effect | | Note |
|---|---|---|
| 3D Glasses | ⬜ | needs a second layer |
| Bevel Alpha | 🟡 | lot 4 |
| Bevel Edges | ⬜ | |
| CC Cylinder, CC Sphere | ⬜ | |
| CC Spotlight | ⬜ | |
| Drop Shadow | ✅ | |
| Radial Shadow | 🟡 | lot 4 |

## Stylize

| Effect | | Note |
|---|---|---|
| Brush Strokes | ⬜ | random strokes |
| Cartoon | ⬜ | |
| CC Block Load | ⬜ | |
| CC Burn Film | ⬜ | |
| CC Glass | ⬜ | |
| CC HexTile | ⬜ | |
| CC Kaleida | ⬜ | |
| CC Mr. Smoothie | ⬜ | |
| CC Plastic | ⬜ | |
| CC RepeTile | ⬜ | |
| CC Threshold, CC Threshold RGB | 🟡 | lot 3 |
| CC Vignette | 🟡 | lot 2 |
| Color Emboss | 🟡 | lot 3 |
| Emboss | ✅ | |
| Find Edges | 🟡 | lot 2 |
| Glow | ✅ | |
| Mosaic | ✅ | |
| Motion Tile | ✅ | |
| Posterize | ✅ | |
| Roughen Edges | ⬜ | parameter list already measured; proprietary noise |
| Scatter | ⬜ | random |
| Strobe Light | ⛔ | depends on time |
| Texturize | ⬜ | needs a texture layer |
| Threshold | 🟡 | lot 2 |

## Transition

| Effect | | Note |
|---|---|---|
| Block Dissolve | ⬜ | random; seed law to measure |
| Card Wipe | ⛔ | 3D cards |
| CC Glass Wipe, CC Image Wipe | ⬜ | need a second layer |
| CC Grid Wipe | ⬜ | |
| CC Jaws | ⬜ | |
| CC Light Wipe | ⬜ | |
| CC Line Sweep | ⬜ | |
| CC Radial ScaleWipe | ⬜ | |
| CC Scale Wipe | ✅ | |
| CC Twister | ⬜ | |
| CC WarpoMatic | ⬜ | |
| Gradient Wipe | ⬜ | needs a gradient layer |
| Iris Wipe | 🟡 | lot 2 |
| Linear Wipe | ✅ | |
| Radial Wipe | 🟡 | lot 1 |
| Venetian Blinds | 🟡 | lot 1 |

## Not covered by this list

Time effects (Echo, Posterize Time, Time Difference, Time Displacement, Timewarp, Pixel Motion Blur, CC Force
Motion Blur, CC Wide Time), 3D effects (CC Particle World, Shatter, Caustics, Wave World, Foam, Card Dance), text and
expression controls, audio effects and the Immersive Video effects are outside fxlib: they need several frames, 3D
geometry or audio, or are not image effects.

## How a batch goes

1. Write the shaders and manifests (`"status": "unverified"`, `"auto": false`) and add their visible settings to
   `rml2ae/ae2rml/fxref/spec.py`. The CI checks that they compile and run.
2. On a Mac with After Effects: `python render_refs.py --params <slugs>` (records the real parameter lists), then
   `python render_refs.py <slugs>` (renders the references). Commit `fxlib/_ae_params.json` and the PNGs.
3. Measure (`fxlib check <slug> --holdout`), fix the shaders, add held-out settings chosen by someone who has not read
   the shader, set `status` and `verified`, then `fxlib regress <slug> --update`.
