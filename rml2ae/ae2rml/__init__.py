"""ae2rml — After Effects project (.aep) -> Rive CLI project (scene.rml), the reverse of rml2ae.

    python -m rml2ae.ae2rml project.aep out_dir [--comp NAME] [--fps N] [--once] [--no-bg] [--verify] [--shot T ...]
    ae import project.aep out_dir …   (same thing through the `ae` command)

Reads the .aep with py-aep (no After Effects needed), evaluates the expressions offline, writes rive.yaml +
scene.rml + assets/, and build/ae2rml/: report.md, effects_todo.json (effects to rebuild as WGSL),
expressions_failed.json, idmap.json (stable ids on re-import) and tag_ae_project.jsx (tags the original AE project
so `ae pull` brings AE edits back into the RML).
"""
