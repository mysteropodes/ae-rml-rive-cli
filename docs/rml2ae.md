# rml2ae: Rive CLI project to After Effects

`rml2ae` converts a Rive CLI project (`rive.yaml`, `scene.rml`, assets) into an After Effects (AE) project. It generates an ExtendScript (`.jsx`) that rebuilds the scene in AE, and AE saves the `.aep` itself. The `ae` command wraps the converter with build, incremental update, pull-back, render and compare commands.

The same `scene.rml` serves both tools. Iterate in the Rive viewer (`rive <project>`, real time) and send the result to After Effects at milestones. An editor file (`.rev`) first becomes a project with `rive create <dir> --from-rev=file.rev`.

This document covers the Rive-to-AE direction. For the reverse (`ae import`, AE project to Rive CLI project) and for the WGSL effect library, see the ae2rml documentation. Setup is in [install.md](install.md); agent setup is in [agents.md](agents.md).

## Contents

1. [Quick start](#quick-start)
2. [Requirements](#requirements)
3. [Comp organisation: `--layout industry` or `raw`](#comp-organisation)
4. [What converts, and how](#what-converts-and-how)
5. [ViewModels, data binding and Luau](#viewmodels-data-binding-and-luau)
6. [The `ae` command](#the-ae-command)
7. [Incremental updates](#incremental-updates)
8. [`ae pull`: bringing AE edits back](#ae-pull-bringing-ae-edits-back)
9. [The After Effects panel](#the-after-effects-panel)
10. [AE effects through RML comments](#ae-effects-through-rml-comments)
11. [Rive Shader plugin](#rive-shader-plugin)
12. [Side files](#side-files)
13. [Measured constants](#measured-constants)
14. [Known limits](#known-limits)

## Quick start

With After Effects open and the install done:

```bash
ae doctor my_project            # every line should read [ok]
ae build my_project             # new AE project + my_project/build/rml2ae/my_project.aep
ae diff my_project --times 1 2.5 4     # AE frames next to `rive --screenshot` (warns if the AE project is colour-managed or > 8 bpc)
```

Without the `ae` command on your PATH, the converter alone runs from the repository root:

```bash
.venv/bin/python -m rml2ae my_project                    # writes the .jsx and the report, does not run AE
.venv/bin/python -m rml2ae my_project --run              # also runs the script in the open AE, which saves the .aep
.venv/bin/python -m rml2ae my_project --shots 1 2.5      # AE frames vs `rive --screenshot`, side by side
```

Options of `python -m rml2ae <project>`:

| Option | Meaning |
|---|---|
| `--out <dir>` | Output folder (default `<project>/build/rml2ae`). |
| `--fps N` | Comp frame rate (default: the most common frame rate among the animations). |
| `--main <Artboard>` | Artboard to use as the main comp (default: the file's default artboard). |
| `--layout industry\|raw` | Comp organisation, see below (default `industry`). |
| `--keep-project` | Build into the open AE project instead of creating a new one. |
| `--incremental` | Incremental update of the open project (implies `--keep-project` and replace). |
| `--no-replay` | Do not replay Luau-scripted content through the Rive CLI. |
| `--all-animations` | Build a comp for every animation, not only the ones the state machine plays. |
| `--duration S` | Duration in seconds for looped content. |
| `--run` | Execute the generated script in the open After Effects. |
| `--shots t1 t2 …` | Compare AE and Rive frames at the given times. |

Outputs are `<name>.jsx`, `<name>.ae-report.md` (what was converted, approximated or left out) and helper PNGs (mesh rows, gradient mattes).

## Requirements

- macOS, After Effects 2024 or later (developed and measured against After Effects 2026), with `aerender`.
- The Rive CLI (`rive`) on the PATH, for verification, screenshots and Luau replay.
- Python 3.9+ with Pillow, NumPy and fontTools (the installer creates the environment).
- ffmpeg, only for `ae render --out file.mp4`.
- The fonts used by the project installed in macOS. AE looks fonts up by PostScript name; `ae doctor <project>` lists the font files.
- In After Effects, the preference **Scripts & Expressions › Allow Scripts to Write Files and Access Network** must be on.
- `rml2ae/schema.json` caches `rive schema --json` (the property keys and names of all types). Regenerate it if the CLI's schema changes.

## Comp organisation

`--layout industry` (the default) builds the project the way a motion designer would, without changing a pixel compared with `--layout raw` (measured on several test projects: 0% of pixels differing by more than 8/255). `raw` creates one layer per Rive element.

With `industry`:

- **One shape layer per group.** A `Node` whose subtree contains only simple shapes becomes a single shape layer. The node's transform and keys live on the layer. Each sub-node and shape inside is a **vector group** with its own keyed transform, and group opacity is native (no opacity expression). Neighbouring isolated shapes share one layer named `Parent · A…B`.
- Kept as separate layers: gradients (Gradient Ramp at layer level), feathered shapes, blend modes, clips, internal data bindings, skins and bones.
- **No useless nulls.** An identity node (no transform, no keys, no clip, no binding) creates no null. A node that carries a single static leaf (shape, text or image without its own rotation or scale) is folded: the leaf takes the node's animation and bindings, and the node's offset moves into the anchor point (exact).
- `ae pull` reads groups too, through `<project>.groups.json` (group path inside its layer to Rive id), and divides the opacity of a folded node by that of its leaf. Keys rewritten identically (2D position of a group, constant x, per-frame baking) are no longer detected as edits: values are compared, not keys.

Remaining nulls are animated controllers (cameras, three-copy RGB split and similar).

**Feather** (Rive CLI 1.3: `Feather` in a `Fill`, `fillRule="clockwise"`) becomes a solid of the fill colour cut by a per-path mask, with a mask feather of 1.25 x the feather strength and no expansion (measured: 0.09% mean difference on an offset drop shadow, a glow and a moving shape). It is placed under or over the shape according to paint order. Inner feather is approximated by a mask (reported). AE's Gaussian Blur is not used here because it is not rendered by `saveFrameToPng`.

## What converts, and how

### Core elements

| RML | After Effects |
|---|---|
| `Artboard` + `Fill` | A comp (dimensions; background = solid). Duration = the played animation, or the longest nested content. |
| State machine entry state, `AnimationState` (+ exit-time chain) | The comp plays that animation. A chain becomes a "sequence" comp of precomps named `Artboard · Animation`. |
| `NestedArtboard` | A precomp (collapsed if the artboard does not clip). `NestedRemapAnimation.time` (0..1) becomes a keyed Time Remap; `NestedSimpleAnimation` becomes a played precomp. |
| `Node` (x, y, rotation in radians, scale, opacity) | A null (or folded/grouped, see above). Children are parented. Opacity is propagated by expression (`value * parent.opacity/100`) when not grouped. |
| `Node` + `ClippingShape` | A precomp of the subtree (in the parent's space, 3x canvas for deep nodes) with a copy of the clip as alpha track matte. |
| `Shape` + `PointsPath` (Straight, CubicDetached, Mirrored, Asymmetric), `Rectangle`, `Ellipse`, `Polygon`, `Star`, `Triangle` | Shape layer, one group per `Shape`. Keyed vertices become path keys. |
| `Fill` / `Stroke` + `SolidColor` (keyed colour), cap/join/thickness, `fillRule`, `TrimPath`, `DashPath` | Fill, Stroke, Trim Paths, Dashes. |
| Gradients | Two stops: Gradient Ramp. More stops, or any gradient with alpha: a gradient PNG with a matte (the Ramp has no alpha). |
| `Image` (+ regular keyed `Mesh`) | Image layer (anchor = origin). A mesh becomes one strip per row plus Corner Pin. |
| `Text` + `TextStylePaint` + `TextValueRun` + `FontAsset` | Text layer (PostScript name read from the TTF, size, leading, tracking, justification, paragraph box). |
| `TextModifierGroup` + `TextModifierRange` | Text animator (Position, Rotation, Opacity, Scale) with a selector (units, index or %, Start/End); `strength` maps to Amount. |
| `LinearAnimation` (hold, linear, cubic `CubicEaseInterpolator`) | AE keys. A CSS-style ease becomes `KeyframeEase(speed, influence)` on both keys of the segment. Other interpolators become linear (approximate). |
| `blendModeValue` | Blend mode (by name or numeric value). |
| `ScriptedDrawable` with `ScriptInputArtboard` | The input artboard placed as a precomp (the script's own rendering is not applied unless replayed, see below). |
| Bones and skins | Bones become nulls (forward kinematics by parenting). Skinned paths are baked per frame. |

### Not converted

`LayoutComponent` (flex layout), state machines with inputs, events and blend states (the entry state's animation is played; a note goes in the report). Anything not converted is listed in `<name>.ae-report.md`.

## ViewModels, data binding and Luau

| RML | After Effects |
|---|---|
| Linked artboard `Fill` (`isVisible`, `opacity`) | A "Background" solid with an expression. |
| `ViewModel` + default instance | A comp `VM · <name>`: a `ViewModel` null with a Slider (number), Checkbox (boolean), Color Control (colour) and a guide text layer (string). Values are those of the default instance. |
| `DataBindContext` (source path ids to property key) | An expression on the AE property: x, y, rotation, scale, opacity, isVisible, fill or stroke colour, thickness, trim, text source, text-animator strength. Converters and nested paths give the raw value plus a note. |
| `ScriptedDrawable` / `ScriptedLayout` without an input artboard | **Replay through the Rive CLI**: a temporary copy of the project keeps only the element and its ancestors, is rendered twice (black and white background), un-matted into straight alpha, and the PNG sequence is imported at the comp's frame rate and placed in world space. `--no-replay` disables it. A cache lives in `build/rml2ae/replay/`. |
| Script with `ScriptInputArtboard` + `context:shader("x")` | If the Rive Shader plugin is installed, the shader runs as is (see below). Otherwise `x.wgsl` is recognised from the fields of its `struct Params` (grain, wobble, displace, vignette, tick) and approximated with native effects (Noise, `wiggle()`, Turbulent Displace, masked vignette) in a comp `<artboard> + FX`, then `+ FX (stepped)` with Posterize Time when the script steps its own clock. Values come from the ViewModel, then from the `num(vm, "x", default)` calls in the Luau. |

Textures produced by another pass of a script (the same artboard instanced with different ViewModel values) are declared in `ae_passes.json`; rml2ae then clones the comp tree per pass with its own `VM · <name> [pass]` copy and retargets the binding expressions.

## The `ae` command

`ae` runs from any directory. It uses the repository's `.venv` and works on a project folder.

```
ae doctor [<project>]
ae templates
ae build <project> [--main X] [--replace [--full]] [--no-replay] [--fps N]
ae watch <project> [--main X]
ae render <project> [--comp X] [--range a-b | --advance t] [--out dir|file.mp4] [--rs T] [--om T]
ae screenshot <project> --advance t [--comp X] [--out file.png]
ae diff <project> --times t1 t2 ...
ae pull <project> [--dry] [--comp X]
ae import <file.aep> <project> [...]
ae help
```

| Command | What it does |
|---|---|
| `doctor [<project>]` | Checks After Effects (and, while it runs, that no dialog blocks it), `aerender`, the Rive CLI, ffmpeg, Python dependencies and the Rive Shader plugin. With a project: that `scene.rml` exists, how many fonts and whether their files are present, and the artboards. Exit code 1 if anything is `[!!]`. |
| `templates` | Lists the render-settings and output-module templates of the running AE (their names are localised). |
| `build <project>` | Converts and runs the script in the open AE: new project, `.aep` saved under `build/rml2ae/`. |
| `build --replace` | Updates the **open** AE project incrementally: only comps whose RML changed are rebuilt, your edits elsewhere stay. `--full` replaces everything. |
| `watch <project>` | Incremental update every time `scene.rml` or an asset (`.rml .png .luau .wgsl .json .ttf`) changes. |
| `render <project>` | Headless render with `aerender` to a PNG sequence (`f[####].png`). `--out file.mp4` encodes through ffmpeg (H.264, yuv420p). `--range a-b` selects frames, `--advance t` one frame at time `t`, `--comp` a comp (default: the top comp of the last build), `--rs` / `--om` render-settings and output-module templates (defaults are the "Best Settings" and "PNG" templates, in the AE language). |
| `screenshot <project> --advance t` | One frame at time `t` (a single-frame render). |
| `diff <project> --times t1 t2 …` | AE frames from the open project next to `rive --screenshot`, as side-by-side images. |
| `pull <project>` | Writes your AE edits back into `scene.rml`. `--dry` lists them only; `--comp X` restricts to one comp. |
| `import <file.aep> <project>` | The reverse direction (ae2rml); needs no AE. See the ae2rml documentation. |

Notes:

- `build`, `watch`, `pull` and `diff` need After Effects running (ExtendScript has no headless mode on macOS). `render` and `screenshot` use `aerender` and need a saved project.
- In `--replace` and `watch` modes, rendering reads the **open** project, which must be saved (the build prints `unsaved` otherwise).
- Save the AE project before `--replace` or `watch`, and do not leave a dialog open in AE during a build: scripts would be blocked.
- When After Effects stops on a modal dialog (a script error, a missing file, a question), `build`, `watch`, `pull` and `diff` stop after about 20 seconds and print the dialog's text, instead of waiting for their timeout. On macOS this reads AE's windows through System Events, which needs the terminal allowed in **System Settings › Privacy & Security › Accessibility**; without it the commands only stop at the timeout. On Windows it needs nothing. `ae doctor` says whether detection works and whether AE is blocked now.
- Logs go to `build/rml2ae/<name>.ae.log`; the report is `build/rml2ae/<name>.ae-report.md`.
- `render` has a start-up cost (about 13 seconds) plus roughly 1.5 seconds per frame in the reference measurements. There is no real-time AE viewer outside AE: `render` and `screenshot` are renders.

## Incremental updates

Every comp carries a tag in its `comment` (`rive:<artboard>|<animation>`, `…|seq`, `…|looped`, `…|clip`, `vm:<id>`). Every layer carries the Rive id of its element (`rive:0:287` on the main layer; `rive:0:287+…` on its auxiliary layers such as gradient PNG or matte; `head+` on a comp's background). **Do not rename or clear these comments**: incremental update and `ae pull` depend on them.

The generated script is split into blocks (comp header, element, child element). Their fingerprints are written to `build/rml2ae/<name>.manifest.json` at the end of each successful build. On `--replace`, `watch` or the panel button, rml2ae compares fingerprints:

- Element unchanged and its layer still there: kept, along with your effects, masks and edits on that layer. Its children are examined in turn.
- Element changed (or you deleted its layer): the old layer subtree is removed and recreated **at the same place in the stack** (otherwise above its previous sibling, otherwise at the bottom of its parent). Children are recreated with it; parenting and inherited opacity expressions are redone.
- Element added: created at its place in drawing order. Element removed from the RML: its layers are removed.
- Comp header changed (size, duration, background): the comp is rebuilt whole and layers using it are relinked with `replaceSource`, so their edits stay. A comp that disappeared is deleted. Already imported media are reused.
- Layers you added in a kept comp (without a `rive:` comment) are never touched.

A key that changes in state A changes the carried-over values in state B, so the affected elements of B are rebuilt too; this is expected. `--full` rebuilds everything.

A dry test that needs no AE: `python3 -m rml2ae.tests.sim_incremental`.

## `ae pull`: bringing AE edits back

Edits you make in the open AE project return to `scene.rml`:

- Scope: the transform of each layer tagged `rive:<id>`: x, y, rotation, scaleX, scaleY, opacity and the hidden (eye) flag.
- As an attribute when the property has no keys in AE; as keys of the comp's animation otherwise. AE eases are converted back to `CubicEaseInterpolator` by exact inversion of the build mapping; linear and hold are kept.
- Static values are read from the comp of the artboard's first animation; keys are read in each animation comp.
- A single key on a property that is not keyed in the RML is treated as a value carried over from an earlier state, and ignored.
- Ignored: matte layers, `[skinned]` layers, clipped precomps and auxiliary `+` layers.
- Groups (industry layout) are read through `<project>.groups.json`.

The RML is rewritten as text (attributes and `<KeyedProperty>` blocks only), then checked with `rive --verify`. The manifest is marked up to date for the pulled elements, so the next `build --replace` does not rebuild them and their AE effects stay.

Not pulled: colours, strokes, paths or text edited in AE; keys that fall between frames (rounded to the animation's frame grid); layers added in AE (they stay in AE and never reach the RML).

## The After Effects panel

`rml2ae/panel/Rive.jsx` is a dockable ScriptUI panel. The installer copies it to the AE `Scripts/ScriptUI Panels` folder and writes the repository path into it. In AE open **Window › Rive.jsx**, choose the project folder, then:

- **Update from RML → this project**: generates the script with Python and runs it in AE, replacing the previous import incrementally.
- **Pull this project's edits → RML**: the `ae pull` operation.
- **Rive viewer**: opens the project in the Rive CLI viewer from a terminal.
- **Report**: opens the conversion report.

The panel is the intended way to update AE when you decide to; the `ae` command is the same engine for terminals, scripts, renders and diffs. If you move the repository, re-run the installer so the panel points at the new location.

## AE effects through RML comments

Rive has no effects (shadows, blurs). Declare them as XML comments immediately before the element that receives them. Rive ignores comments (`rive --verify` accepts them); rml2ae places native AE effects on the element's main layer.

```xml
<!-- ae: DropShadow x=2 y=6 blur=12 color=#33000000 -->
<!-- ae: GaussianBlur radius=8 -->
<!-- ae: Glow color=#FFFFFF radius=20 intensity=1 -->
<!-- ae: Tint black=#000000 white=#FFFFFF amount=100 -->
<!-- ae: effect "ADBE Gaussian Blur 2" 0001=20 0002=1 -->
<Shape name="card" ...>
```

- Colours are `#AARRGGBB`, or `color=#000000 opacity=0.2`.
- The last form is raw: any effect `matchName` followed by its parameter indices and values.
- Several comments stack in order. Note that stacked Drop Shadows composite on top of each other (measured), so multiple Figma-style shadows should be merged into one.
- Measured in After Effects 2026: Drop Shadow opacity is 0..255; direction is in degrees clockwise from the top; softness is about the Figma blur radius, with alpha scaled by 0.8 to match Figma density; Gaussian Blur radius is about equal to the Figma radius (doubling it erases a 200 px band).
- Inner shadow has no scriptable native effect (layer styles cannot be scripted): it is reported, not applied.
- The effect library is `rml2ae/effects.py`.

Related rules measured while converting vector boards:

- Gradient Ramp points are in comp coordinates and ignore parenting, so a Ramp is used only on a shape that is neither parented nor rotated; otherwise a gradient PNG with a matte is used (also for any gradient with alpha).
- Rive draws the **last** paint of a shape on top; AE draws the **topmost** item of the group on top. Paints are therefore added in reverse order.
- A clipping source without a paint (Rive clips by geometry) receives a white fill so it can serve as a matte.
- A JPEG file named `.png` cannot be imported by AE.

## Rive Shader plugin

The Rive Shader plugin is an After Effects effect that runs a Rive `.wgsl` post-process shader unchanged on a layer. When it is installed, rml2ae uses it for scripts that call `context:shader("x")` instead of approximating with native effects; without it, the approximations above are used. `ae doctor` reports whether it is installed. Build, install and parameter conventions are documented in [rive-shader-plugin.md](rive-shader-plugin.md).

## Side files

Optional files next to `scene.rml`:

- `ae_passes.json`: texture passes of a post-process (ViewModel overrides per pass and per texture).
- `ae_audio.json`: audio layers, for example `[{"comp": "Main", "file": "build/soundtrack.wav", "at": 0}]`.

## Measured constants

Measured against After Effects 2026 with the Montserrat typeface; useful when writing text by hand.

- Rive: first baseline = top of the box + hhea ascender x size; the bounds height of one line = 1.164 x size.
- AE: `addBoxText` centres the box on the layer origin; first baseline = box top + 0.743 x size.
- Keyframe order: BEZIER everywhere, then eases, then HOLD last.
- Setting `layer.parent` rewrites the child's whole transform; set position, rotation and scale afterwards.
- A null is a solid at 0% opacity.
- A collapsed precomp with a track matte is rasterised at its comp's size.

## Known limits

- Bezier ease interpolators other than CSS-style cubic ones become linear.
- Gradients with more than two stops, or with alpha, are baked as PNG mattes rather than native gradients.
- Flex layouts, state machines with inputs and events, and blend states are not converted; only the entry state's animation is built.
- Luau-scripted content is replayed as a PNG sequence (not editable in AE).
- Without the Rive Shader plugin, shaders are approximated by native effects and only the common patterns are recognised.
- Inner shadows and AE layer styles cannot be written.
- `ae pull` covers transforms, keys, eases and visibility only.
- Text metrics depend on fonts installed in macOS and on the constants above.
- One After Effects at a time: the `ae` command acts on the AE that is open.
- Every conversion writes a report; read `<name>.ae-report.md` after each build to see what is `exact`, `approx` or `unsupported` before trusting pixel parity.
