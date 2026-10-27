"""Every fxlib effect in the Rive CLI: `rive --verify` on its test project (fxlib.test_project), then one headless
capture, which runs the script.

    python tools/fxlib_rive_verify.py [slug ...] [--no-run]

The fxlib regression (fxlib regress) runs the WGSL with wgpu, whose naga parser accepts what the Rive CLI's WGSL
compiler rejects (a WGSL keyword such as `uniform` as a field name, `a * b ^ c` without parentheses): an effect can
pass there and not build in Rive at all. `--verify` only compiles, so the capture catches what fails when the script
runs (CLI 1.8.0: a bind group that fills a binding the shader never reads is refused, and the effect draws nothing);
the CLI prints script errors even with --quiet. Exit code 1 when an effect does not build or its script fails.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rml2ae.ae2rml import fxlib  # noqa: E402

SRC = os.path.join(ROOT, "rml2ae", "ae2rml", "fxref", "src")


SCRIPT_ERROR = re.compile(r"^[\w.-]+:\d+: ")       # "fx_bevel_alpha:106: GPUBindGroup.new: ..."


def main(args):
    run = "--no-run" not in args
    slugs = [a for a in args if not a.startswith("--")] or fxlib.all_slugs()
    tmp = tempfile.mkdtemp(prefix="fxlib_rive_")
    env = {**os.environ, "RIVE_NO_TUI": "1"}
    bad = []
    for slug in slugs:
        d = os.path.join(tmp, slug)
        fxlib.test_project(slug, {}, d, os.path.join(SRC, "src_premult.png"), map_png=os.path.join(SRC, "map.png"))
        v = subprocess.run(["rive", d, "--verify"], capture_output=True, text=True, env=env)
        if v.returncode != 0:
            errs = [ln.strip() for ln in (v.stdout + v.stderr).splitlines() if "error" in ln]
            bad.append(slug)
            print(f"FAIL {slug}: " + " | ".join(errs)[:600])
            continue
        if run:
            r = subprocess.run(["rive", d, f"--screenshot={os.path.join(d, 'shot.png')}", "--artboard=Main", "--advance=0.1s",
                                "--quiet"], capture_output=True, text=True, env=env)
            errs = sorted({ln.strip() for ln in (r.stdout + r.stderr).splitlines() if SCRIPT_ERROR.match(ln.strip())})
            if r.returncode != 0 or errs or not os.path.exists(os.path.join(d, "shot.png")):
                bad.append(slug)
                print(f"FAIL {slug} (running): " + " | ".join(errs or [(r.stdout + r.stderr).strip()[-300:]])[:600])
    print(f"fxlib_rive_verify: {len(slugs)} effect(s), {len(bad)} not building or failing in the Rive CLI: {' '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
