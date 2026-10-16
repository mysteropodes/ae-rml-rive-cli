"""Every fxlib effect through the Rive CLI's compiler: `rive --verify` on its test project (fxlib.test_project).

    python tools/fxlib_rive_verify.py [slug ...]

The fxlib regression (fxlib regress) runs the WGSL with wgpu, whose naga parser accepts what the Rive CLI's WGSL
compiler rejects (a WGSL keyword such as `uniform` as a field name, `a * b ^ c` without parentheses): an effect can
pass there and not build in Rive at all. Exit code 1 when one does not build.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rml2ae.ae2rml import fxlib  # noqa: E402

SRC = os.path.join(ROOT, "rml2ae", "ae2rml", "fxref", "src")


def main(slugs):
    slugs = slugs or fxlib.all_slugs()
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
    print(f"fxlib_rive_verify: {len(slugs)} effect(s), {len(bad)} not building in the Rive CLI: {' '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
