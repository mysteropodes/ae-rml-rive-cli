"""Les notes de relecture, côté ligne de commande : ouvrir le viewer, ramasser ce que the author
annote dedans, le ranger dans .review/notes.json, le relire. Voir README.md § « Annoter ».

Le viewer écrit les `print()` des scripts sur sa sortie standard (mesuré) : ReviewNotes y envoie
une ligne par note (`#ANN {…}`) et par suppression (`#ANNDEL id`). Ce module lit ce flux.

    python3 review_notes.py open <projet> "DemoB Review"     # ouvre le viewer et ramasse en direct
    python3 review_notes.py open <projet> "…" --background   # idem, mais rend la main (pour un agent)
    python3 review_notes.py open <projet> "…" --viewport=1150x709   # fenêtre à la taille voulue (demi-format)
    python3 review_notes.py status <projet>                  # la session tourne-t-elle ? combien de notes ?
    python3 review_notes.py stop <projet>                    # ferme la session lancée en arrière-plan
    python3 review_notes.py watch <projet> < viewer.log       # même chose depuis un log déjà ouvert
    python3 review_notes.py list <projet>                     # relit les notes rangées
    python3 review_notes.py list <projet> --new               # seulement celles arrivées depuis la dernière fois
    python3 review_notes.py rebuild <projet>                  # reconstruit le JSON depuis review_notes.log
    python3 review_notes.py list <projet> --format=md         # pour coller dans une réponse

Le fichier `<projet>/.review/notes.json` : {"notes": [{id, t, text, pts, at}]} — `t` en secondes
dans le film, `pts` = x1,y1,x2,y2,… aplati en coordonnées de l'artboard de relecture (-1,-1 = fin
d'un trait), `at` = horodatage de la prise de note.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time

ANN = re.compile(r"#ANN (\{.*\})\s*$")
DEL = re.compile(r"#ANNDEL (\d+)\s*$")
RES = re.compile(r"#ANNRES (\d+) ([01])\s*$")


# Les notes vivent dans `<projet>/.review/` et PAS à la racine du projet : le viewer du CLI
# reconstruit la scène dès qu'un fichier du dossier bouge (mesuré) — écrire une note à la racine
# faisait recharger le film, qui repartait à 0 en perdant le fil. Un dossier caché est ignoré.
def dir_for(project):
    d = os.path.join(project, ".review")
    os.makedirs(d, exist_ok=True)
    return d


def path_for(project):
    return os.path.join(dir_for(project), "notes.json")


def log_for(project):
    """le journal : une ligne par événement, on n'y efface jamais rien — c'est le filet
    (un JSON perdu, écrasé par une synchro, se reconstruit avec `rebuild`)"""
    return os.path.join(dir_for(project), "notes.log")


def migrate(project):
    """déménage un ancien review_notes.json/.log posé à la racine ; renvoie ce qui a bougé"""
    moved = []
    for old, new in (("review_notes.json", path_for(project)), ("review_notes.log", log_for(project))):
        o = os.path.join(project, old)
        if os.path.exists(o) and not os.path.exists(new):
            os.replace(o, new)
            moved.append(old)
        elif os.path.exists(o):
            os.remove(o)             # le nouveau fait foi
            moved.append(old + " (doublon retiré)")
    return moved


def append_log(project, line):
    with open(log_for(project), "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + line.rstrip("\n") + "\n")


def load(project):
    migrate(project)
    p = path_for(project)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"notes": []}


def save(project, data):
    """écriture atomique : un fichier temporaire puis un remplacement, jamais un fichier à moitié écrit"""
    p = path_for(project)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def bbox(pts):
    xs = [pts[i] for i in range(0, len(pts), 2) if pts[i] >= 0]
    ys = [pts[i] for i in range(1, len(pts), 2) if pts[i] >= 0]
    if not xs:
        return None
    return [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]


def feed(data, line, stamp=None):
    """une ligne du viewer -> (kind, note) si elle en portait une"""
    m = ANN.search(line)
    if m:
        try:
            note = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        note["at"] = stamp or time.strftime("%Y-%m-%d %H:%M:%S")
        note["box"] = bbox(note.get("pts", []))
        same = [n for n in data["notes"] if n.get("id") == note.get("id")]
        # même id ET même instant = correction de la même note ; même id à un autre instant =
        # une nouvelle session repartie à 1, on la renumérote au lieu d'écraser
        if same and abs(same[0].get("t", 0) - note.get("t", 0)) > 0.05:
            note["id"] = max((n.get("id", 0) for n in data["notes"]), default=0) + 1
            same = []
        if same:
            note["done"] = same[0].get("done", False)
        data["notes"] = [n for n in data["notes"] if n.get("id") != note.get("id")] + [note]
        data["notes"].sort(key=lambda n: n.get("t", 0))
        return ("ann", note)
    m = RES.search(line)
    if m:
        i, done = int(m.group(1)), m.group(2) == "1"
        for n in data["notes"]:
            if n.get("id") == i:
                n["done"] = done
                return ("res", n)
        return None
    m = DEL.search(line)
    if m:
        i = int(m.group(1))
        gone = [n for n in data["notes"] if n.get("id") == i]
        data["notes"] = [n for n in data["notes"] if n.get("id") != i]
        return ("del", gone[0] if gone else {"id": i})
    return None


def pid_path(project):
    return os.path.join(dir_for(project), "session.pid")


def out_path(project):
    return os.path.join(dir_for(project), "session.out")


def seen_path(project):
    return os.path.join(dir_for(project), "seen")


def running(project):
    """le pid de la session en arrière-plan, s'il y en a une qui tourne"""
    p = pid_path(project)
    if not os.path.exists(p):
        return None
    try:
        pid = int(open(p).read().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        return None


def show(note):
    t = note.get("t", 0)
    tick = "[x] " if note.get("done") else "[ ] "
    txt = note.get("text") or "(sans texte)"
    box = note.get("box")
    where = f"  [{box[0]},{box[1]} → {box[2]},{box[3]}]" if box else ""
    return f"{tick}{t:7.2f} s  #{note.get('id')}  {txt}{where}"


def pump(project, stream, echo=True):
    """le JSON sur le disque fait foi : on le relit avant chaque écriture, pour ne jamais écraser
    ce qu'une autre session (ou moi) y a mis entre deux notes"""
    data = load(project)
    for raw in stream:
        # le CLI imprime chaque print deux fois : la ligne brute, puis « [hh:mm] console Script:12  … »
        if "] console " in raw:
            continue
        if not (ANN.search(raw) or DEL.search(raw) or RES.search(raw)):
            continue
        append_log(project, raw)          # d'abord le journal, ensuite le JSON
        data = load(project)              # relire : le fichier a pu bouger depuis la note précédente
        got = feed(data, raw)
        if not got:
            continue
        kind, note = got
        save(project, data)
        if echo:
            mark = {"ann": "+ ", "del": "- ", "res": "· "}[kind]
            print(mark + show(note), flush=True)
    return data


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, project = argv[0], argv[1]
    if cmd == "open":
        artboard = next((a for a in argv[2:] if not a.startswith("-")), None)
        # passed through to the viewer: --viewport=WxH (window size, e.g. half size) and --fit=
        extra = [a for a in argv[2:] if a.startswith("--viewport=") or a.startswith("--fit=")]
        if "--background" in argv:
            # la session vit sa vie : l'humain annote dans le viewer, l'agent relit `list --new`
            if running(project):
                print(f"une session tourne déjà (pid {running(project)}) — `stop` d'abord")
                return 1
            here = os.path.abspath(__file__)
            out = open(out_path(project), "a")
            args = [sys.executable, here, "open", project] + ([artboard] if artboard else []) + extra
            child = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
            with open(pid_path(project), "w") as f:
                f.write(str(child.pid))
            print(f"session {child.pid} lancée — les notes vont dans {path_for(project)}")
            print(f"  python3 {os.path.basename(here)} list {project} --new     # ce qui est arrivé depuis")
            print(f"  python3 {os.path.basename(here)} stop {project}")
            return 0
        fit = [a for a in extra if a.startswith("--fit=")] or ["--fit=contain"]
        args = ["rive", "."] + fit + [a for a in extra if a.startswith("--viewport=")] + ([f"--artboard={artboard}"] if artboard else [])
        print(f"viewer : {' '.join(args)}  (dans {project})\nles notes arrivent ici et vont dans {path_for(project)}\n", flush=True)
        p = subprocess.Popen(args, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.PIPE, text=True, bufsize=1)
        try:
            data = pump(project, p.stdout or [])
        except KeyboardInterrupt:
            data = load(project)
        finally:
            p.terminate()
            if os.path.exists(pid_path(project)) and running(project) in (None, os.getpid()):
                os.remove(pid_path(project))
        print(f"\n{len(data['notes'])} note(s) dans {path_for(project)}")
        return 0
    if cmd == "status":
        pid = running(project)
        data = load(project)
        print(f"session : {'en cours (pid %d)' % pid if pid else 'aucune'}")
        print(f"notes   : {len(data['notes'])} dans {path_for(project)}")
        if pid:
            print(f"sortie  : {out_path(project)}")
        return 0
    if cmd == "stop":
        pid = running(project)
        if not pid:
            print("aucune session en arrière-plan")
            return 0
        try:
            # tout le groupe : le viewer est un enfant, un SIGTERM au seul parent le laisserait orphelin
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            os.kill(pid, signal.SIGTERM)
        try:
            os.remove(pid_path(project))
        except OSError:
            pass
        print(f"session {pid} arrêtée — {len(load(project)['notes'])} note(s)")
        return 0
    if cmd == "watch":
        data = pump(project, sys.stdin)
        print(f"{len(data['notes'])} note(s) dans {path_for(project)}")
        return 0
    if cmd == "rebuild":
        data = {"notes": []}
        p = log_for(project)
        if not os.path.exists(p):
            print(f"pas de journal dans {p}")
            return 1
        with open(p) as f:
            for line in f:
                feed(data, line, stamp=line[:19])
        save(project, data)
        print(f"{len(data['notes'])} note(s) reconstruite(s) depuis {p}")
        return 0
    if cmd == "list":
        data = load(project)
        md, only_new = "--format=md" in argv, "--new" in argv
        notes = data["notes"]
        if only_new:
            # « depuis la dernière fois » : l'horodatage de prise de note, gardé dans .review/seen
            last = ""
            if os.path.exists(seen_path(project)):
                last = open(seen_path(project)).read().strip()
            notes = [n for n in notes if str(n.get("at", "")) > last]
            stamps = [str(n.get("at", "")) for n in data["notes"] if n.get("at")]
            if stamps:
                with open(seen_path(project), "w") as f:
                    f.write(max(stamps))
        for n in notes:
            print(("- " if md else "") + show(n))
        if not notes:
            print("aucune note nouvelle" if only_new else "aucune note")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
