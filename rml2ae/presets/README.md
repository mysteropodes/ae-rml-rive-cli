Animation presets saved from After Effects, used as templates for native shape gradients (see `rml2ae/ffx.py`):

- `gfill.ffx`: a shape group named `GF` holding a rectangle and a Gradient Fill.
- `gstroke.ffx`: a shape group named `GS` holding an ellipse and a Gradient Stroke.

To rebuild them: run `build_template.jsx` (this folder) in AE, select the group `GF` (then `GS`) of the layer
`template` and save it with Animation > Save Animation Preset under these names. Without them, rml2ae falls back to
a Gradient Ramp or a matted PNG.
