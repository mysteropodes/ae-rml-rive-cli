---
name: ae2rml
description: Convert an After Effects project (.aep) into a Rive CLI project (rive.yaml + scene.rml) without After Effects, and check how close it is. Use when the user wants an .aep in Rive, asks what of an AE project survives in Rive, or wants AE effects as WGSL.
---

# ae2rml — After Effects (.aep) → Rive CLI project

    .venv/bin/python -m rml2ae.ae2rml project.aep out_dir [--comp NAME] [--fps N] [--verify] [--shot T ...]

## Rules
- Never modify, move or re-save the user's .aep. ae2rml only READS it (py-aep). If After Effects must render a
  reference, work on a COPY of the .aep in a scratch folder, and never save it.
- Always run with `--verify` (Rive CLI check of the result) and read `out_dir/build/ae2rml/report.md`: it lists what
  was converted, approximated (≈) or left out (✗). Report the approximations; do not claim parity blindly.
- Compare against After Effects only with real renders: capture Rive with `--shot T` (it captures at T + 1/60 s, the
  Rive CLI screenshot shows the animation one 60 Hz step behind `--advance`).
- Effects without a Rive equivalent: if they are in `rml2ae/ae2rml/fxlib/` (manifest `status`: exact / close / approx)
  they become a WGSL node; manifests with `"auto": false` are measured worse than leaving the effect out and stay in
  `build/ae2rml/effects_todo.json`.
- Expressions are evaluated offline; those that fail or use AE's private random generator are listed in
  `expressions_failed.json` and can be sampled once in AE with `build/ae2rml/bake_expressions.jsx` (read-only).
- py-aep cannot read some old projects: ask the user to re-save a copy in a recent After Effects.
