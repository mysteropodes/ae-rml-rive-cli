---
name: rml2ae
description: Convert a Rive CLI project (rive.yaml + scene.rml) into an After Effects project with the `ae` command (build, incremental update, pull AE edits back, render, compare). Use when the user mentions After Effects, AE, .aep, or wants a Rive CLI scene in After Effects.
---

# rml2ae — Rive CLI project → After Effects

`ae` (installed by rml2ae/install.sh) drives After Effects from a Rive CLI project. Same scene.rml for both:
author and iterate in the Rive viewer (`rive <project>`), send to AE at milestones.

## Rules
- Never run `ae build`, `ae watch` or `ae pull` without the user's explicit go: they act on the After Effects the user
  has open (build writes into the open project in `--replace` mode, pull reads it). Ask first, every time.
- After Effects must be running for build/watch/pull (ExtendScript has no headless mode); `ae render`/`ae screenshot`
  use aerender and need the project saved.
- Before any AE work: `ae doctor <project>` — every line must be [ok].
- After a build: read `<project>/build/rml2ae/<name>.ae-report.md`. "unsupported" and "approx" entries say what AE
  cannot express and how rml2ae approximated it (Luau scripts, IK, flex layouts are replayed by the Rive CLI as
  image sequences unless `--no-replay`). Tell the user what was approximated; do not claim pixel parity blindly.
- The RML is the source of truth. Edits made inside AE are only kept if they are on layers rml2ae did not rebuild:
  `ae build --replace` rebuilds only elements whose RML changed (a layer keeps its effects/masks when its element is
  unchanged). To bring AE transform/keyframe edits back into the RML: `ae pull --dry` then `ae pull`.

## Commands
```
ae doctor <project>                      environment check
ae build <project> [--main <Artboard>] [--no-replay] [--fps N]      new AE project + build/rml2ae/<name>.aep
ae build <project> --replace [--full]    update the OPEN AE project incrementally (--full: rebuild everything)
ae watch <project>                       incremental update whenever scene.rml / assets change
ae pull <project> [--dry]                AE edits (x/y/rotation/scale/opacity/hidden, keys + eases) → scene.rml
ae render <project> [--range a-b] [--out file.mp4 | dir]            aerender (PNG sequence / mp4 via ffmpeg)
ae screenshot <project> --advance t [--out file.png]
ae diff <project> --times t1 t2 ...      AE frames vs `rive --screenshot`, side by side
```

## How the conversion maps (what to expect in AE)
- artboard → comp per animation played by the default state machine's entry state; exit-time chains → a sequence comp;
  loops → a "(looped)" comp. NestedArtboard → precomp + Time Remap. Clipped node → precomp + alpha matte.
- Shapes → shape layers (paths, fills, strokes, dashes, trim, gradients: 2 stops = Gradient Ramp, more = PNG matte).
  Text → text layers (box text, runs, animators). Images → footage (+ mesh strips). Bones → nulls (FK by parenting),
  skinned paths baked per frame. ViewModels → a "VM · <name>" control comp, data binds → expressions.
- Every layer carries its Rive id in `comment` (`rive:0:287`); comps are tagged the same way. Do not rename those.
- Luau post-process shaders: with the Rive Shader plugin, the .wgsl runs as is; without it, native AE effects approximate.

## AE effects in the RML
Rive has no shadows/blurs. Write them as comments right before the element: `<!-- ae: DropShadow x=2 y=6 blur=12
color=#33000000 -->`, `<!-- ae: GaussianBlur radius=8 -->`, `<!-- ae: effect "ADBE Gaussian Blur 2" 0001=20 -->`.
Rive ignores them; `ae build` turns them into native AE effects. Inner shadows have no scriptable AE equivalent.
`figma2rml` writes them from Figma effects.

## Side files next to a project (optional)
- `ae_audio.json`: `[{"comp": "Main", "file": "build/soundtrack.wav", "at": 0}]` → audio layers.
- `ae_passes.json`: ViewModel overrides per post-process pass.

Docs: rml2ae/README.md (mapping, incremental, pull, limits), INSTALL.md, and RIVE_CLI_MCP_KNOWLEDGE.md for authoring RML.

## Motion references (no images needed)
`python3 -m rml2ae.tools.riv_curves <file.riv …> [--json out.json]` reads any .riv (Rive community files, client
files) and prints the curves actually keyed (ease catalogue, segment durations, overshoots, staggers, transition blend
times, state-machine/rig/binding structure, recurring patterns). Feed new references to `motion/MOTION_PRINCIPLES.md`
and use `motion/motion_lib.py` (named eases + recipes → RML keys) / `motion/rml_motion.py` (keys → full RML timelines,
transitions, VM conditions) when animating. Keys are poses, not frames: never write a key per frame unless the motion
is procedural, and then run `python3 -m motion.reduce_keys scene.rml --dry` to get the poses + eases back.
