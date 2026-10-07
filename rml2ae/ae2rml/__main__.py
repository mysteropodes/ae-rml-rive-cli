"""python -m rml2ae.ae2rml project.aep out_dir [--comp NAME] [--fps N] [--once] [--no-bg] [--verify] [--shot T …]
                                              [--media-scale S] [--media-fps N]

--media-scale S   videos and image sequences are exported as image sequences (one ImageAsset per frame shown): scale
                  their frames — and still images — by S (default 1 = footage size) to keep the .riv light
--media-fps N     at most N distinct images per second of footage (default: one per comp frame)
--no-audio        no sound (audio layers and the sound of videos are left out)
--no-video        video files (.mov, .mp4…) are left out: their layers draw nothing (image sequences and stills stay)
--work-area       the main comp's animation ends with its work area (what AE renders by default)
--ae-lang fr|en|any  the After Effects UI language to emulate: AE resolves names in expressions (effect("C")("Color"))
                  in its own language only, a French AE disables English ones (default: AE2RML_LANG, else the system's)"""
import os
import subprocess
import sys
import time


def main(argv):
    args, flags, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a in ("--comp", "--fps", "--media-scale", "--media-fps", "--ae-lang"):
            flags[a[2:]] = argv[i + 1]
            i += 2
            continue
        if a == "--shot":
            flags.setdefault("shot", [])
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                flags["shot"].append(argv[i])
                i += 1
            continue
        if a.startswith("--"):
            flags[a[2:]] = True
        else:
            args.append(a)
        i += 1
    if len(args) < 1 or "help" in flags or "h" in flags:
        print(__doc__)
        return 1
    base = os.environ.get("AE_CWD") or os.getcwd()          # bin/ae runs from the repository root: paths are the caller's
    aep = os.path.join(base, args[0])
    out = os.path.join(base, args[1] if len(args) > 1 else
                       os.path.splitext(os.path.basename(aep))[0].replace(" ", "_") + "_rive")
    from .convert import Converter
    t0 = time.time()
    conv = Converter(aep, out, comp=flags.get("comp"), fps=int(flags["fps"]) if flags.get("fps") else None,
                     loop="oneShot" if flags.get("once") else "loop", bg=not flags.get("no-bg"),
                     media_scale=float(flags.get("media-scale") or 1.0),
                     media_fps=float(flags["media-fps"]) if flags.get("media-fps") else None,
                     ae_lang=flags.get("ae-lang"), audio=not flags.get("no-audio"),
                     video=not flags.get("no-video"), work_area=bool(flags.get("work-area")))
    scene = conv.convert()
    c = conv.report.counts()
    print(f"{scene}  ({time.time() - t0:.1f}s)")
    print(f"  artboards {len(conv.order)} · main '{conv.main.ab.name}' · converted {c['converted']} · approx {c['approx']} · "
          f"unsupported {c['unsupported']} · effects to rebuild {len(conv.report.todo_effects)} · "
          f"expressions kept out {len(conv.engine.failed)}")
    print(f"  report: {os.path.join(conv.build_dir, 'report.md')}")
    rc = 0
    if flags.get("verify") or flags.get("shot"):
        v = subprocess.run(["rive", out, "--verify"], capture_output=True, text=True)
        tail = (v.stdout + v.stderr).strip().splitlines()[-6:]
        print("  rive --verify: " + ("ok" if v.returncode == 0 else "FAILED"))
        for line in tail:
            print("    " + line)
        rc = v.returncode
    for t in flags.get("shot", []):
        png = os.path.join(out, "build", f"shot_{t}.png")
        # the Rive CLI screenshot shows its animation one 60 Hz step behind --advance (the first step only enters the
        # state machine; measured on walk-test: Rive at T + 1/60 = AE at T to the pixel): compare AE at T with Rive at T + 1/60
        sec = float(t[:-2]) / 1000.0 if t.endswith("ms") else float(t.rstrip("s"))
        adv = f"{sec + 1.0 / 60.0:.4f}s"
        r = subprocess.run(["rive", out, f"--screenshot={png}", f"--advance={adv}"], capture_output=True, text=True)
        print(f"  shot {t}: {png if r.returncode == 0 else 'FAILED ' + (r.stdout + r.stderr)[-200:]}")
    return rc


def cli():
    """Console-script entry point (pyproject.toml)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
