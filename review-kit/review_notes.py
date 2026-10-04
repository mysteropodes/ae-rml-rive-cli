"""Review notes, command-line side: open the viewer, collect what the reviewer
annotates in it, store it in .review/notes.json, read it back. See README.md, "Annoter" section.

The viewer writes scripts' `print()` output to its standard output (measured): ReviewNotes sends
one line per note (`#ANN {…}`) and per deletion (`#ANNDEL id`). This module reads that stream.

    python3 review_notes.py open <project> "My Review"       # opens the viewer and collects live
    python3 review_notes.py open <project> "…" --background  # same, but returns control (for an agent)
    python3 review_notes.py open <project> "…" --viewport=1150x709   # window at the wanted size (half size)
    python3 review_notes.py status <project>                 # is the session running? how many notes?
    python3 review_notes.py stop <project>                   # closes the session started in the background
    python3 review_notes.py watch <project> < viewer.log      # same thing from an already open log
    python3 review_notes.py list <project>                    # reads the stored notes back
    python3 review_notes.py list <project> --new              # only those that arrived since last time
    python3 review_notes.py rebuild <project>                 # rebuilds the JSON from review_notes.log
    python3 review_notes.py list <project> --format=md        # to paste into a reply

The file `<project>/.review/notes.json`: {"notes": [{id, t, text, pts, at}]} — `t` in seconds
in the film, `pts` = x1,y1,x2,y2,… flattened, in review-artboard coordinates (-1,-1 = end
of a stroke), `at` = timestamp of when the note was taken.
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


# Notes live in `<project>/.review/` and NOT at the project root: the CLI viewer
# rebuilds the scene as soon as a file in the folder changes (measured) — writing a note at the root
# made the film reload, restarting at 0 and losing its place. A hidden folder is ignored.
def dir_for(project):
    d = os.path.join(project, ".review")
    os.makedirs(d, exist_ok=True)
    return d


def path_for(project):
    return os.path.join(dir_for(project), "notes.json")


def log_for(project):
    """the journal: one line per event, nothing is ever erased from it — it is the safety net
    (a lost JSON, overwritten by a sync, can be rebuilt with `rebuild`)"""
    return os.path.join(dir_for(project), "notes.log")


def migrate(project):
    """moves an old review_notes.json/.log left at the root; returns what was moved"""
    moved = []
    for old, new in (("review_notes.json", path_for(project)), ("review_notes.log", log_for(project))):
        o = os.path.join(project, old)
        if os.path.exists(o) and not os.path.exists(new):
            os.replace(o, new)
            moved.append(old)
        elif os.path.exists(o):
            os.remove(o)             # the new one is authoritative
            moved.append(old + " (duplicate removed)")
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
    """atomic write: a temporary file then a replace, never a half-written file"""
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
    """a viewer line -> (kind, note) if it carried one"""
    m = ANN.search(line)
    if m:
        try:
            note = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        note["at"] = stamp or time.strftime("%Y-%m-%d %H:%M:%S")
        note["box"] = bbox(note.get("pts", []))
        same = [n for n in data["notes"] if n.get("id") == note.get("id")]
        # same id AND same time = correction of the same note; same id at another time =
        # a new session that restarted at 1, so renumber it instead of overwriting
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
    """the pid of the background session, if one is running"""
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
    txt = note.get("text") or "(no text)"
    box = note.get("box")
    where = f"  [{box[0]},{box[1]} → {box[2]},{box[3]}]" if box else ""
    return f"{tick}{t:7.2f} s  #{note.get('id')}  {txt}{where}"


def pump(project, stream, echo=True):
    """the JSON on disk is authoritative: reload it before each write, to never overwrite
    what another session (or a person) put there between two notes"""
    data = load(project)
    for raw in stream:
        # the CLI prints each print twice: the raw line, then "[hh:mm] console Script:12  …"
        if "] console " in raw:
            continue
        if not (ANN.search(raw) or DEL.search(raw) or RES.search(raw)):
            continue
        append_log(project, raw)          # journal first, then the JSON
        data = load(project)              # reload: the file may have changed since the previous note
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
            # the session runs on its own: the human annotates in the viewer, the agent re-reads `list --new`
            if running(project):
                print(f"a session is already running (pid {running(project)}) — run `stop` first")
                return 1
            here = os.path.abspath(__file__)
            out = open(out_path(project), "a")
            args = [sys.executable, here, "open", project] + ([artboard] if artboard else []) + extra
            child = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
            with open(pid_path(project), "w") as f:
                f.write(str(child.pid))
            print(f"session {child.pid} started — notes go to {path_for(project)}")
            print(f"  python3 {os.path.basename(here)} list {project} --new     # what has arrived since")
            print(f"  python3 {os.path.basename(here)} stop {project}")
            return 0
        fit = [a for a in extra if a.startswith("--fit=")] or ["--fit=contain"]
        args = ["rive", "."] + fit + [a for a in extra if a.startswith("--viewport=")] + ([f"--artboard={artboard}"] if artboard else [])
        print(f"viewer: {' '.join(args)}  (in {project})\nnotes arrive here and go to {path_for(project)}\n", flush=True)
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
        print(f"\n{len(data['notes'])} note(s) in {path_for(project)}")
        return 0
    if cmd == "status":
        pid = running(project)
        data = load(project)
        print(f"session : {'running (pid %d)' % pid if pid else 'none'}")
        print(f"notes   : {len(data['notes'])} in {path_for(project)}")
        if pid:
            print(f"output  : {out_path(project)}")
        return 0
    if cmd == "stop":
        pid = running(project)
        if not pid:
            print("no background session")
            return 0
        try:
            # the whole group: the viewer is a child, a SIGTERM to the parent alone would leave it orphaned
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            os.kill(pid, signal.SIGTERM)
        try:
            os.remove(pid_path(project))
        except OSError:
            pass
        print(f"session {pid} stopped — {len(load(project)['notes'])} note(s)")
        return 0
    if cmd == "watch":
        data = pump(project, sys.stdin)
        print(f"{len(data['notes'])} note(s) in {path_for(project)}")
        return 0
    if cmd == "rebuild":
        data = {"notes": []}
        p = log_for(project)
        if not os.path.exists(p):
            print(f"no journal in {p}")
            return 1
        with open(p) as f:
            for line in f:
                feed(data, line, stamp=line[:19])
        save(project, data)
        print(f"{len(data['notes'])} note(s) rebuilt from {p}")
        return 0
    if cmd == "list":
        data = load(project)
        md, only_new = "--format=md" in argv, "--new" in argv
        notes = data["notes"]
        if only_new:
            # "since last time": the timestamp of when the note was taken, kept in .review/seen
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
            print("no new notes" if only_new else "no notes")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
