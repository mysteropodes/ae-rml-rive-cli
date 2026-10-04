"""Poser l'outil de relecture sur un projet Rive CLI qui n'a rien prévu pour.

Le générateur `review_timeline.py` s'appelle depuis un gen_scene.py maison ; ce module-ci fait le
même travail sur un `scene.rml` déjà écrit : il y lit l'artboard du film, son animation, sa police,
son audio, puis ajoute (ou remplace) un artboard « <Film> Review » entre deux marqueurs.

    python3 review_install.py <projet>                       # installe, devine tout
    python3 review_install.py <projet> --artboard="Mon film" --parts "0:Intro,7.1:Refrain"
    python3 review_install.py <projet> --refresh             # recharge les notes prises depuis
    python3 review_install.py <projet> --remove              # enlève l'artboard de relecture

Ce que la commande touche dans le projet :
    scene.rml            l'artboard de relecture, entre <!-- review-kit:start --> et :end
    ReviewPlayer.luau    copiés depuis ce dossier (le CLI compile tous les .luau du projet)
    ReviewNotes.luau
    .review/config.json  les réglages, pour rejouer l'installation avec --refresh

Le bloc est délimité : réinstaller ne duplique rien, et --remove rend le projet à son état d'avant.
Les ids sont pris dans un préfixe libre (« 7777:N » par défaut), donc sans collision possible avec
ceux du projet.
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
    """[(tag, span_debut, span_fin_du_bloc)] — un artboard par entrée, dans l'ordre du fichier"""
    out = []
    for m in re.finditer(r"<Artboard\b[^>]*>", rml):
        out.append((m.group(0), m.start()))
    return out


def artboard_body(rml, start):
    """le texte de l'artboard qui commence à `start`, jusqu'à son </Artboard> (imbrication comprise)"""
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
            continue                                  # un artboard de travail, pas un film
        nm = _attr(tag, "name")
        if name is None or nm == name:
            return tag, pos, nm
    raise SystemExit(f"artboard introuvable : {name!r}" if name else "aucun artboard dans scene.rml")


def pick_animation(body, name=None):
    best = None
    for m in re.finditer(r"<LinearAnimation\b[^>]*>", body):
        tag = m.group(0)
        if name and _attr(tag, "name") != name:
            continue
        dur = float(_attr(tag, "duration") or 0)
        if best is None or dur > float(_attr(best, "duration") or 0):
            best = tag                                # la plus longue : c'est le film, pas un cycle
    if best is None:
        raise SystemExit("cet artboard n'a pas de LinearAnimation : rien à relire")
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
    """« 0:Intro,7.1:Refrain » -> [(0.0, "Intro"), (7.1, "Refrain")] ; sinon un seul bloc"""
    if not spec:
        return [(0.0, fallback)]
    out = []
    for chunk in spec.split(","):
        t, _, label = chunk.partition(":")
        try:
            out.append((float(t), label.strip() or f"{float(t):.1f}s"))
        except ValueError:
            raise SystemExit(f"--parts : « {chunk} » n'est pas « seconde:nom »")
    out.sort()
    return out


def find_font(project):
    """une police posée dans le projet (hors build/) : chemin relatif au projet, comme dans le RML"""
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
        raise SystemExit(f"pas de scene.rml dans {project}")
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
        raise SystemExit(f"artboard {film_name!r} : largeur/hauteur/durée illisibles")

    font, font_id = cfg.get("font"), None
    if not font:
        m = re.search(r'<FontAsset\b[^>]*\bfile="([^"]*)"[^>]*\bid="([^"]*)"', rml)
        if m:
            font, font_id = m.group(1), m.group(2)   # déjà déclarée : on réutilise son id
    if not font:
        font = find_font(project)                    # une .ttf posée dans le projet fait l'affaire
    if not font:
        raise SystemExit("le panneau a besoin d'une police et la scène n'en déclare aucune :\n"
                         "  --font=<chemin vers une .ttf, relatif au projet>")

    audio, audio_name = cfg.get("audio"), cfg.get("audio_name") or "soundtrack"
    audio_declared = False
    silent = audio == ""                             # --no-audio : le projet joue son son lui-même
    if silent:
        audio = None
    elif audio is None:
        m = re.search(r'<AudioAsset\b[^>]*\bfile="([^"]*)"[^>]*\bname="([^"]*)"', rml)
        if m:
            audio, audio_name, audio_declared = m.group(1), m.group(2), True
    # la police et l'audio sont déjà déclarés dans la scène : les redéclarer ferait deux assets
    parts = parse_parts(cfg.get("parts"), duration, film_name or "Film")
    name = cfg.get("name") or f"{film_name} Review"

    # un artboard ne peut être imbriqué que promu composant (inspect : nested-artboard-not-component)
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
        raise SystemExit("scene.rml : pas de </Rive> — fichier inattendu")
    open(scene, "w", encoding="utf-8").write(rml[:end] + block + rml[end:])

    cfg.update({"promoted": promoted or cfg.get("promoted", False),
                "artboard": film_name, "name": name, "font": font, "audio": "" if silent else audio,
                "audio_name": audio_name, "fps": fps})
    save_config(project, cfg)
    print(f"{'remplacé' if had else 'installé'} : artboard « {name} » "
          f"({film_name}, {w:.0f}x{h:.0f}, {duration:.1f} s, {len(parts)} bloc(s)"
          f"{', audio' if audio else ''})")
    print(f"  rive {project} --artboard=\"{name}\"      # ou, pour ramasser les notes :")
    print(f"  python3 {os.path.join(HERE, 'review_notes.py')} open {project} \"{name}\"")


def remove(project):
    scene = os.path.join(project, "scene.rml")
    rml, had = strip_block(open(scene, encoding="utf-8").read())
    if not had:
        print("rien à enlever")
        return
    cfg = load_config(project)
    if cfg.get("promoted"):                      # l'artboard n'était pas un composant avant nous
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
    print("artboard de relecture et scripts retirés (les notes restent dans .review/)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("project")
    ap.add_argument("--artboard", help="le film à relire (par défaut : le premier exporté)")
    ap.add_argument("--animation", help="son animation (par défaut : la plus longue)")
    ap.add_argument("--name", help="nom de l'artboard de relecture (par défaut « <Film> Review »)")
    ap.add_argument("--parts", help='les blocs de la barre : "0:Intro,7.1:Refrain"')
    ap.add_argument("--font", help="la police du panneau (par défaut : la première de la scène)")
    ap.add_argument("--audio", help="la bande-son à jouer (par défaut : celle de la scène)")
    ap.add_argument("--no-audio", action="store_true", help="ne rien jouer (une scène dont le son est piloté par script)")
    ap.add_argument("--fps", type=float)
    ap.add_argument("--refresh", action="store_true", help="réinstalle avec les réglages gardés (recharge les notes)")
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
