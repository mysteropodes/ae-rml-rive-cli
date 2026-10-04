"""Install the review tool on a Rive CLI project that made no provision for it.

The generator `review_timeline.py` is called from a custom gen_scene.py; this module does the
same job on an already written `scene.rml`: it reads the film's artboard, its animation, its font,
its audio, then adds (or replaces) a "<Film> Review" artboard between two markers.

    python3 review_install.py <project>                      # installs, guesses everything
    python3 review_install.py <project> --artboard="My film" --parts "0:Intro,7.1:Chorus"
    python3 review_install.py <project> --refresh            # reloads the notes taken since
    python3 review_install.py <project> --remove             # removes the review artboard

What the command touches in the project:
    scene.rml            the review artboard, between <!-- review-kit:start --> and :end
    ReviewPlayer.luau    copied from this folder (the CLI compiles every .luau of the project)
    ReviewNotes.luau
    .review/config.json  the settings, to replay the installation with --refresh

The block is delimited: reinstalling duplicates nothing, and --remove returns the project to its previous state.
Ids are taken from a free prefix ("7777:N" by default), so there can be no collision with
those of the project.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from review_timeline import review_rml, install_player, load_notes  # noqa: E402

START, END = "<!-- review-kit:start -->", "<!-- review-kit:end -->"


def _attr(tag, name):
    m = re.search(r'\b%s="([^"]*)"' % name, tag)
    return m.group(1) if m else None


def artboards(rml):
    """[(tag, start_pos)] — one artboard per entry, in file order"""
    out = []
    for m in re.finditer(r"<Artboard\b[^>]*>", rml):
        out.append((m.group(0), m.start()))
    return out


def artboard_body(rml, start):
    """the text of the artboard starting at `start`, up to its </Artboard> (nesting included)"""
    depth, i = 0, start
    for m in re.finditer(r"</?Artboard\b", rml[start:]):
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return rml[start:start + m.end() + len("Artboard>")]
        else:
            depth += 1
    return rml[start:]


def pick_artboard(rml, name=None):
    for tag, pos in artboards(rml):
        if _attr(tag, "includeInExport") == "false":
            continue                                  # a working artboard, not a film
        nm = _attr(tag, "name")
        if name is None or nm == name:
            return tag, pos, nm
    raise SystemExit(f"artboard not found: {name!r}" if name else "no artboard in scene.rml")


def pick_animation(body, name=None):
    best = None
    for m in re.finditer(r"<LinearAnimation\b[^>]*>", body):
        tag = m.group(0)
        if name and _attr(tag, "name") != name:
            continue
        dur = float(_attr(tag, "duration") or 0)
        if best is None or dur > float(_attr(best, "duration") or 0):
            best = tag                                # the longest: that is the film, not a cycle
    if best is None:
        raise SystemExit("this artboard has no LinearAnimation: nothing to review")
    return best


def free_prefix(rml, wanted="7777"):
    used = set(re.findall(r'id="(\d+):', rml))
    p = int(wanted)
    while str(p) in used:
        p += 1
    return str(p)


def ids_from(prefix):
    n = {"v": 0}

    def nxt():
        n["v"] += 1
        return f"{prefix}:{n['v']}"
    return nxt


def strip_block(rml):
    a, b = rml.find(START), rml.find(END)
    if a == -1 or b == -1:
        return rml, False
    return rml[:a].rstrip("\n") + "\n" + rml[b + len(END):].lstrip("\n"), True


def parse_parts(spec, duration, fallback):
    """\"0:Intro,7.1:Chorus\" -> [(0.0, \"Intro\"), (7.1, \"Chorus\")]; otherwise a single block"""
    if not spec:
        return [(0.0, fallback)]
    out = []
    for chunk in spec.split(","):
        t, _, label = chunk.partition(":")
        try:
            out.append((float(t), label.strip() or f"{float(t):.1f}s"))
        except ValueError:
            raise SystemExit(f"--parts: \"{chunk}\" is not \"second:name\"")
    out.sort()
    return out


def find_font(project):
    """a font placed in the project (outside build/): path relative to the project, as in the RML"""
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in ("build", ".review", ".git")]
        for f in sorted(files):
            if f.lower().endswith((".ttf", ".otf")):
                return os.path.relpath(os.path.join(root, f), project)
    return None


def config_path(project):
    return os.path.join(project, ".review", "config.json")


def load_config(project):
    p = config_path(project)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def save_config(project, cfg):
    os.makedirs(os.path.dirname(config_path(project)), exist_ok=True)
    with open(config_path(project), "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def install(project, cfg):
    scene = os.path.join(project, "scene.rml")
    if not os.path.exists(scene):
        raise SystemExit(f"no scene.rml in {project}")
    rml = open(scene, encoding="utf-8").read()
    rml, had = strip_block(rml)

    tag, pos, film_name = pick_artboard(rml, cfg.get("artboard"))
    body = artboard_body(rml, pos)
    anim = pick_animation(body, cfg.get("animation"))
    film_id, anim_id = _attr(tag, "id"), _attr(anim, "id")
    w, h = float(_attr(tag, "width") or 0), float(_attr(tag, "height") or 0)
    fps = float(cfg.get("fps") or _attr(anim, "fps") or 60)
    frames = float(_attr(anim, "duration") or 0)
    duration = frames / fps
    if not (w and h and duration):
        raise SystemExit(f"artboard {film_name!r}: width/height/duration unreadable")

    font, font_id = cfg.get("font"), None
    if not font:
        m = re.search(r'<FontAsset\b[^>]*\bfile="([^"]*)"[^>]*\bid="([^"]*)"', rml)
        if m:
            font, font_id = m.group(1), m.group(2)   # already declared: reuse its id
    if not font:
        font = find_font(project)                    # a .ttf placed in the project will do
    if not font:
        raise SystemExit("the panel needs a font and the scene declares none:\n"
                         "  --font=<path to a .ttf, relative to the project>")

    audio, audio_name = cfg.get("audio"), cfg.get("audio_name") or "soundtrack"
    audio_declared = False
    silent = audio == ""                             # --no-audio: the project plays its own sound
    if silent:
        audio = None
    elif audio is None:
        m = re.search(r'<AudioAsset\b[^>]*\bfile="([^"]*)"[^>]*\bname="([^"]*)"', rml)
        if m:
            audio, audio_name, audio_declared = m.group(1), m.group(2), True
    # the font and audio are already declared in the scene: redeclaring them would create two assets
    parts = parse_parts(cfg.get("parts"), duration, film_name or "Film")
    name = cfg.get("name") or f"{film_name} Review"

    # an artboard can only be nested once promoted to a component (inspect: nested-artboard-not-component)
    promoted = False
    if 'isComponent="true"' not in tag:
        rml = rml[:pos] + tag.replace("<Artboard", '<Artboard isComponent="true"', 1) + rml[pos + len(tag):]
        promoted = True

    install_player(project)
    art, roots = review_rml(ids_from(free_prefix(rml, cfg.get("idPrefix", "7777"))),
                            film_id, anim_id, w, h, duration, parts,
                            font_file=font, audio_file=audio, audio_name=audio_name,
                            name=name, x=float(cfg.get("x", 0)), y=float(cfg.get("y", -3600)),
                            notes=load_notes(project), fps=fps,
                            font_id=font_id, audio_declared=audio_declared)
    component = f'<ComponentAsset artboardId="{film_id}" name="{film_name}"/>'
    block = f"\n{START}\n{art}\n{roots}\n{component}\n{END}\n"
    end = rml.rfind("</Rive>")
    if end == -1:
        raise SystemExit("scene.rml: no </Rive> — unexpected file")
    open(scene, "w", encoding="utf-8").write(rml[:end] + block + rml[end:])

    cfg.update({"promoted": promoted or cfg.get("promoted", False),
                "artboard": film_name, "name": name, "font": font, "audio": "" if silent else audio,
                "audio_name": audio_name, "fps": fps})
    save_config(project, cfg)
    print(f"{'replaced' if had else 'installed'}: artboard \"{name}\" "
          f"({film_name}, {w:.0f}x{h:.0f}, {duration:.1f} s, {len(parts)} block(s)"
          f"{', audio' if audio else ''})")
    print(f"  rive {project} --artboard=\"{name}\"      # or, to collect the notes:")
    print(f"  python3 {os.path.join(HERE, 'review_notes.py')} open {project} \"{name}\"")


def remove(project):
    scene = os.path.join(project, "scene.rml")
    rml, had = strip_block(open(scene, encoding="utf-8").read())
    if not had:
        print("nothing to remove")
        return
    cfg = load_config(project)
    if cfg.get("promoted"):                      # the artboard was not a component before us
        try:
            tag, pos, _ = pick_artboard(rml, cfg.get("artboard"))
            rml = rml[:pos] + tag.replace(' isComponent="true"', "", 1) + rml[pos + len(tag):]
        except SystemExit:
            pass
    open(scene, "w", encoding="utf-8").write(rml)
    for f in ("ReviewPlayer.luau", "ReviewNotes.luau"):
        p = os.path.join(project, f)
        if os.path.exists(p):
            os.remove(p)
    print("review artboard and scripts removed (the notes stay in .review/)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("project")
    ap.add_argument("--artboard", help="the film to review (default: the first exported one)")
    ap.add_argument("--animation", help="its animation (default: the longest)")
    ap.add_argument("--name", help="name of the review artboard (default \"<Film> Review\")")
    ap.add_argument("--parts", help='blocks of the bar: "0:Intro,7.1:Chorus"')
    ap.add_argument("--font", help="the panel font (default: the first one in the scene)")
    ap.add_argument("--audio", help="the soundtrack to play (default: the scene's)")
    ap.add_argument("--no-audio", action="store_true", help="play nothing (a scene whose sound is driven by script)")
    ap.add_argument("--fps", type=float)
    ap.add_argument("--refresh", action="store_true", help="reinstall with the saved settings (reloads the notes)")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args(argv)
    if a.remove:
        remove(a.project)
        return 0
    cfg = load_config(a.project) if a.refresh else {}
    if getattr(a, "no_audio", False):
        cfg["audio"] = ""
    for k in ("artboard", "animation", "name", "parts", "font", "audio", "fps"):
        v = getattr(a, k)
        if v is not None:
            cfg[k] = v
    install(a.project, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
