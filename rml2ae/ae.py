"""`ae` — a Rive-CLI-like command line for After Effects, built on rml2ae.

  ae doctor [<project>]                 AE running? aerender? rive? plugin Rive Shader? fonts? deps?
  ae templates                          render-settings / output-module templates of the running AE
  ae build <project> [--main X] [--replace [--full]] [--no-replay] [--fps N]
                                        scene.rml -> .jsx -> executed in the running AE (new project + .aep saved;
                                        --replace: update the OPEN project incrementally — only the comps whose RML
                                        changed are rebuilt, your AE edits elsewhere stay; --full replaces everything)
  ae watch <project> [--main X]         incremental update whenever scene.rml / assets change
  ae render <project> [--comp X] [--range a-b | --advance t] [--out dir|file.mp4] [--rs T] [--om T]
                                        headless render with aerender (PNG sequence; .mp4 through ffmpeg)
  ae screenshot <project> --advance t [--comp X] [--out file.png]
  ae diff <project> --times t1 t2 ...   AE frames vs `rive --screenshot`, side by side
  ae pull <project> [--dry] [--comp X]  what you changed in the OPEN AE project (transforms, keys, eases, hidden)
                                        goes back into scene.rml; --dry only lists it
  ae import <file.aep> <project> [--comp X] [--fps N] [--once] [--no-bg] [--verify] [--shot t …]
                                        the other way: an After Effects project -> a Rive CLI project (ae2rml, no AE
                                        needed); build/ae2rml/tag_ae_project.jsx then tags the AE project for `ae pull`

Same scene.rml for Rive and AE: iterate in the Rive viewer (`rive <project>`), check AE at milestones.
AE must be running for build/watch (ExtendScript has no headless mode on macOS); render/screenshot use aerender.
"""
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from rml2ae import aeapp  # noqa: E402

AE_APPS = aeapp.APPS
AE_APP = aeapp.APP
AERENDER = aeapp.AERENDER
RS_DEFAULTS = ["Paramètres optimaux", "Best Settings"]
OM_PNG_DEFAULTS = ["PNG", "PNG Sequence", "Séquence TIFF avec alpha", "TIFF Sequence with Alpha"]


def ae_running():
    return aeapp.running()


def parse(argv):
    args, flags = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            k = a[2:]
            if k in ("main", "fps", "comp", "range", "advance", "out", "rs", "om"):
                flags[k] = argv[i + 1]
                i += 2
                continue
            if k == "times":
                flags[k] = []
                i += 1
                while i < len(argv) and not argv[i].startswith("--"):
                    flags[k].append(float(argv[i]))
                    i += 1
                continue
            flags[k] = True
        else:
            args.append(a)
        i += 1
    return args, flags


# ------------------------------------------------------------------ commands
def cmd_doctor(args, flags):
    ok = True

    def line(label, good, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '!!'}] {label}{(' — ' + detail) if detail else ''}")
    print("ae doctor")
    line("After Effects app", AE_APP is not None, AE_APP or f"not found in {aeapp.WHERE}")
    line("After Effects running", ae_running(), "" if ae_running() else "open AE for build/watch")
    line("aerender", bool(AERENDER and os.path.exists(AERENDER)), AERENDER or "")
    rv = shutil.which("rive")
    ver = subprocess.run(["rive", "--version"], capture_output=True, text=True).stdout.strip() if rv else ""
    line("rive CLI", bool(rv), ver or "brew install --cask rive-app/tap/rive-cli")
    line("ffmpeg", bool(shutil.which("ffmpeg")), "needed for --out .mp4")
    try:
        import PIL, fontTools  # noqa
        line("python deps (PIL, fontTools)", True)
    except Exception as e:
        line("python deps (PIL, fontTools)", False, str(e))
    plug = glob.glob(os.path.join(os.path.expanduser("~"), "AE-Dev-Plugins", aeapp.PLUGIN_NAME)) + \
        (glob.glob(os.path.join(aeapp.PLUGINS, "**", aeapp.PLUGIN_NAME), recursive=True) if aeapp.PLUGINS else [])
    line("Rive Shader plugin", bool(plug), plug[0] if plug else "not installed: shaders fall back to native AE effects")
    if args:
        p = os.path.abspath(args[0])
        line("project scene.rml", os.path.exists(os.path.join(p, "scene.rml")), p)
        from rml2ae.model import Project
        try:
            proj = Project(p)
            fonts = [a.get("file") for a in proj.assets.values() if a.tag == "FontAsset"]
            missing = [f for f in fonts if not os.path.exists(os.path.join(p, f))]
            line(f"fonts ({len(fonts)})", not missing, ", ".join(missing) if missing else "all files present; install them in macOS for AE")
            line(f"artboards ({len(proj.artboards)})", True, ", ".join(a.name for a in proj.artboards[:8]))
        except Exception as e:
            line("project parse", False, str(e))
    return 0 if ok else 1


def cmd_templates(args, flags):
    if not ae_running():
        print("After Effects must be running")
        return 1
    from rml2ae.jsx import js
    tmp = os.path.join(os.path.expanduser("~/.cache/rml2ae"), "templates.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    jsx = tmp + ".jsx"
    open(jsx, "w", encoding="utf-8").write(f'''var out = []; try {{ app.beginUndoGroup("ae templates"); var comp = app.project.items.addComp("__ae_tpl", 16, 16, 1, 1, 25);
var item = app.project.renderQueue.items.add(comp); out.push("render settings: " + item.templates.join(" | ")); out.push("output modules: " + item.outputModule(1).templates.join(" | "));
item.remove(); comp.remove(); app.endUndoGroup(); }} catch (e) {{ out.push("ERR " + e.toString()); }}
var f = new File({js(tmp)}); f.open("w"); f.write(out.join("\\n")); f.close();''')
    if os.path.exists(tmp):
        os.remove(tmp)
    from rml2ae import run as runner
    runner.osa(jsx)
    for _ in range(30):
        if os.path.exists(tmp):
            break
        time.sleep(1)
    print(open(tmp, encoding="utf-8").read() if os.path.exists(tmp) else "no answer from AE")
    return 0


def build(project, flags, replace=False, incremental=False):
    from rml2ae.model import Project
    from rml2ae.convert import Converter
    from rml2ae import run as runner
    if not ae_running():
        print("After Effects must be running (ExtendScript has no headless mode on macOS)")
        return 1
    proj = Project(project)
    out_dir = os.path.join(project, "build", "rml2ae")
    os.makedirs(out_dir, exist_ok=True)
    conv = Converter(proj, out_dir, fps=float(flags["fps"]) if "fps" in flags else None, main=flags.get("main"),
                     keep_project=replace or incremental, replay="no-replay" not in flags, replace=replace or incremental)
    conv.incremental_mode = incremental
    text = conv.convert()
    if incremental:
        for l in conv.stats_lines():
            print(l)
    jsx = os.path.join(out_dir, proj.name + ".jsx")
    open(jsx, "w", encoding="utf-8").write(text)
    open(os.path.join(out_dir, proj.name + ".ae-report.md"), "w", encoding="utf-8").write(conv.rep.markdown())
    t0 = time.time()
    log = runner.run(jsx, conv.log)
    for l in log.splitlines():
        if l.startswith("project "):
            open(os.path.join(out_dir, proj.name + ".project.txt"), "w", encoding="utf-8").write(l[8:].strip())
    tail = [l for l in log.splitlines() if "FAILED" in l or "DONE" in l or "saved" in l]
    print(f"build {proj.name}: {time.time() - t0:.0f}s — " + ("; ".join(tail[-3:]) if tail else log[-300:]))
    return 0 if "DONE" in log and "FAILED" not in log else 1


def cmd_build(args, flags):
    # --replace: rebuild inside the open project; incremental unless --full (only changed comps are rebuilt)
    return build(os.path.abspath(args[0]), flags, replace="replace" in flags or "full" in flags, incremental=("replace" in flags and "full" not in flags))


def cmd_watch(args, flags):
    project = os.path.abspath(args[0])
    print(f"watching {project} (Ctrl-C to stop) — every change rebuilds inside the open AE project, replacing the previous import")

    def stamp():
        files = glob.glob(os.path.join(project, "*.rml")) + glob.glob(os.path.join(project, "*.png")) + glob.glob(os.path.join(project, "*.luau")) \
            + glob.glob(os.path.join(project, "*.wgsl")) + glob.glob(os.path.join(project, "*.json")) + glob.glob(os.path.join(project, "*.ttf"))
        return {f: os.path.getmtime(f) for f in files}
    last = stamp()
    build(project, flags, incremental=True)
    while True:
        time.sleep(1)
        cur = stamp()
        if cur != last:
            time.sleep(1)                     # let the editor finish writing
            cur = stamp()
            changed = [os.path.basename(f) for f in cur if cur.get(f) != last.get(f)] + [os.path.basename(f) for f in last if f not in cur]
            print(f"\nchanged: {', '.join(changed)}")
            last = cur
            build(project, flags, incremental=True)


def find_template(name, defaults):
    return name or defaults[0]


def cmd_render(args, flags, single=None):
    project = os.path.abspath(args[0])
    from rml2ae.model import Project
    proj = Project(project)
    aep = os.path.join(project, "build", "rml2ae", proj.name + ".aep")
    pf = os.path.join(project, "build", "rml2ae", proj.name + ".project.txt")
    if os.path.exists(pf):                       # the last build's AE project (the open one in --replace / watch mode)
        last = open(pf, encoding="utf-8").read().strip()
        if last == "unsaved":
            print("the AE project of the last build is unsaved: save it in AE (Cmd-S), then render again")
            return 1
        aep = last
    if not os.path.exists(aep):
        print(f"no {aep}: run `ae build` first")
        return 1
    topf = os.path.join(project, "build", "rml2ae", proj.name + ".topcomp.txt")
    comp = flags.get("comp") or (open(topf, encoding="utf-8").read().strip() if os.path.exists(topf) else proj.default_artboard().name)
    fps = 25.0
    try:
        chain, _, _ = proj.entry_chain(proj.default_artboard())
        fps = chain[0].fps if chain else 25.0
    except Exception:
        pass
    if single is not None:
        f0 = f1 = int(round(single * fps))
    elif "advance" in flags:
        f0 = f1 = int(round(float(flags["advance"]) * fps))
    elif "range" in flags:
        a, b = flags["range"].split("-")
        f0, f1 = int(a), int(b)
    else:
        f0, f1 = 0, None
    out = flags.get("out")
    mp4 = out and out.lower().endswith(".mp4")
    seq_dir = os.path.join(project, "build", "rml2ae", "render") if (not out or mp4) else (out if os.path.isdir(out) or not os.path.splitext(out)[1] else os.path.dirname(out))
    seq_dir, aep = os.path.abspath(seq_dir), os.path.abspath(aep)      # aerender resolves relative paths against its own folder (measured)
    os.makedirs(seq_dir, exist_ok=True)
    for f in glob.glob(os.path.join(seq_dir, "f*.png")):
        os.remove(f)
    cmd = [AERENDER, "-project", aep, "-comp", comp, "-RStemplate", find_template(flags.get("rs"), RS_DEFAULTS),
           "-OMtemplate", find_template(flags.get("om"), OM_PNG_DEFAULTS), "-output", os.path.join(seq_dir, "f[####].png"), "-s", str(f0)]
    if f1 is not None:
        cmd += ["-e", str(f1)]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    errs = [l for l in r.stdout.splitlines() + r.stderr.splitlines() if "ERROR" in l]
    frames = sorted(glob.glob(os.path.join(seq_dir, "f*.png")))
    print(f"aerender: {len(frames)} frames in {time.time() - t0:.0f}s" + ("; " + "; ".join(errs) if errs else ""))
    if errs and not frames:
        print("templates available: `ae templates` (French AE: 'Paramètres optimaux' / 'PNG')")
        return 1
    if single is not None or "advance" in flags:
        dst = out if (out and out.lower().endswith(".png")) else os.path.join(seq_dir, f"{comp}_{f0:04d}.png")
        if frames:
            shutil.copy(frames[0], dst)
            print("wrote", dst)
    elif mp4:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps), "-start_number", str(f0), "-i", os.path.join(seq_dir, "f%04d.png"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17", out], check=False)
        print("wrote", out)
    return 0


def cmd_screenshot(args, flags):
    if "advance" not in flags:
        print("--advance <seconds> required")
        return 1
    return cmd_render(args, flags, single=float(flags["advance"]))


def cmd_diff(args, flags):
    from rml2ae import run as runner
    from rml2ae.model import Project
    project = os.path.abspath(args[0])
    proj = Project(project)
    main_ab = proj.default_artboard()
    if not ae_running():
        print("After Effects must be running (frames come from the open project)")
        return 1
    topf = os.path.join(project, "build", "rml2ae", proj.name + ".topcomp.txt")
    top = open(topf, encoding="utf-8").read().strip() if os.path.exists(topf) else main_ab.name
    for p in runner.shots(project, os.path.join(project, "build", "rml2ae"), top, main_ab.name, flags.get("times", [1.0])):
        print("wrote", p)
    return 0


def cmd_pull(args, flags):
    from rml2ae import run as runner
    from rml2ae.pull import pull
    project = os.path.abspath(args[0])
    if not ae_running() and "apply" not in flags and "prepare" not in flags:
        print("After Effects must be running (the open project is read)")
        return 1
    out_dir = os.path.join(project, "build", "rml2ae")
    os.makedirs(out_dir, exist_ok=True)
    if "prepare" in flags:                      # the AE panel: python writes the dump script, AE runs it, then --apply
        from rml2ae.pull import prepare
        print(prepare(out_dir)[0])
        return 0
    if "apply" in flags:
        from rml2ae.pull import apply
        changes = apply(project, out_dir, dry="dry" in flags, only_comp=flags.get("comp"))
    else:
        changes = pull(project, out_dir, runner.run, dry="dry" in flags, only_comp=flags.get("comp"))
    if not changes:
        print("nothing to pull: the RML already matches the open project")
        return 0
    print(("would change (--dry):" if "dry" in flags else "pulled into the RML:"))
    for c in changes:
        print("  " + c)
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "import":                              # its own flags (see rml2ae/ae2rml/__main__.py)
        from rml2ae.ae2rml.__main__ import main as ae2rml_main
        return ae2rml_main(rest)
    args, flags = parse(rest)
    fn = {"doctor": cmd_doctor, "templates": cmd_templates, "build": cmd_build, "watch": cmd_watch, "render": cmd_render,
          "screenshot": cmd_screenshot, "diff": cmd_diff, "pull": cmd_pull}.get(cmd)
    if fn is None:
        print(f"unknown command {cmd}\n{__doc__}")
        return 1
    if cmd not in ("doctor", "templates") and not args:
        print("project directory required")
        return 1
    try:
        return fn(args, flags)
    except KeyboardInterrupt:
        return 0


def cli():
    """Console-script entry point (pyproject.toml)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
