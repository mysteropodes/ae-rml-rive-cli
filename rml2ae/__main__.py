"""rml2ae — convert a Rive CLI project to an After Effects script.

  python3 -m rml2ae <project_dir> [--out <dir>] [--fps N] [--main <Artboard>] [--keep-project] [--no-replay] [--all-animations] [--duration S] [--incremental] [--layout industry|raw]
  python3 -m rml2ae <project_dir> --run                    # also execute in AE 2026 (only when allowed)
  python3 -m rml2ae <project_dir> --shots 1.0 2.5 ...      # AE frames vs `rive --screenshot`, side by side

Writes <out>/<name>.jsx  (default out = <project>/build/rml2ae), <out>/<name>.ae-report.md (+ mesh row PNGs); AE saves <out>/<name>.aep.
Run from the folder that contains rml2ae/ (the repository root) — a Python with PIL + fontTools is needed for text metrics
and meshes (`.venv/bin/python`).
"""
import os
import sys

from .model import Project
from .convert import Converter
from . import run as runner


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {}
    i = 0
    rest = list(argv)
    while i < len(rest):
        a = rest[i]
        if a in ("--out", "--fps", "--main", "--duration", "--layout"):
            flags[a[2:]] = rest[i + 1]
            args = [x for x in args if x != rest[i + 1]]
            i += 2
            continue
        if a == "--shots":
            flags["shots"] = []
            i += 1
            while i < len(rest) and not rest[i].startswith("--"):
                flags["shots"].append(float(rest[i]))
                args = [x for x in args if x != rest[i]]
                i += 1
            continue
        if a.startswith("--"):
            flags[a[2:]] = True
        i += 1
    if not args:
        print(__doc__)
        return 1
    pdir = os.path.abspath(args[0])
    proj = Project(pdir)
    out_dir = os.path.abspath(flags.get("out") or os.path.join(pdir, "build", "rml2ae"))
    os.makedirs(out_dir, exist_ok=True)
    incremental = "incremental" in flags
    conv = Converter(proj, out_dir, fps=float(flags["fps"]) if "fps" in flags else None, main=flags.get("main"),
                     keep_project=("keep-project" in flags) or incremental, replay="no-replay" not in flags, replace=("replace" in flags) or incremental,
                     layout=flags.get("layout", "industry"))
    conv.incremental_mode = incremental
    conv.all_animations = "all-animations" in flags
    if "duration" in flags:
        conv.loop_duration = float(flags["duration"])
    jsx_text = conv.convert()
    if incremental:
        for l in conv.stats_lines():
            print(l)
    jsx = os.path.join(out_dir, proj.name + ".jsx")
    open(jsx, "w", encoding="utf-8").write(jsx_text)
    rep = os.path.join(out_dir, proj.name + ".ae-report.md")
    open(rep, "w", encoding="utf-8").write(conv.rep.markdown(f"comp fps {conv.fps} · main artboard '{conv.main_artboard().name}' · output {jsx}"))
    print("wrote", jsx, f"({len(jsx_text.splitlines())} lines)")
    print("wrote", rep)
    from collections import Counter
    c = Counter(k for _, k, _, _ in conv.rep.entries)
    print("report:", dict(c))
    if "run" in flags:
        print(runner.run(jsx, conv.log))
    open(os.path.join(out_dir, proj.name + ".topcomp.txt"), "w", encoding="utf-8").write(conv.top_name or conv.main_artboard().name)
    if "shots" in flags:
        main_ab = conv.main_artboard()
        for p in runner.shots(pdir, out_dir, conv.top_name or main_ab.name, main_ab.name, flags["shots"], conv.fps):
            print("wrote", p)
    return 0


def cli():
    """Console-script entry point (pyproject.toml)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
