"""Execute a .jsx in After Effects (macOS: AppleScript DoScriptFile, Windows: AfterFX.exe -r), render comparison frames
(AE vs `rive --screenshot`)."""
import os
import subprocess
import time

from . import aeapp
from .jsx import js


RIVE_SHOT_LAG = 1.0 / 60.0     # see shots(): the Rive CLI screenshot is one 60 Hz step behind its --advance


def osa(jsx):
    aeapp.run_script(jsx)


def run(jsx, log, timeout=900):
    if os.path.exists(log):
        os.remove(log)
    osa(jsx)
    t0 = time.time()
    watch = aeapp.DialogWatch()
    while time.time() - t0 < timeout:
        if os.path.exists(log):
            txt = open(log, errors="replace", encoding="utf-8").read()
            if "DONE" in txt or "TOP FAILED" in txt:
                return txt
        dialog = watch.check()
        if dialog:              # After Effects waits for a click: the log will not come
            return f"TOP FAILED After Effects shows a dialog: {dialog}\n" + \
                (open(log, errors="replace", encoding="utf-8").read() if os.path.exists(log) else "")
        time.sleep(2)
    return "timeout\n" + (open(log, errors="replace", encoding="utf-8").read() if os.path.exists(log) else "")


def shots(project_dir, out_dir, comp_name, artboard, times, fps=25):
    """AE frames of `comp_name` and CLI screenshots of `artboard` at the same seconds -> side-by-side sheet."""
    shot_dir = os.path.join(out_dir, "shots")
    os.makedirs(shot_dir, exist_ok=True)
    lines = ['var out = [];', 'var comp = null; for (var i = 1; i <= app.project.numItems; i++) { var it = app.project.item(i); if (it instanceof CompItem && it.name == ' + js(comp_name) + ') comp = it; }',
             'if (!comp) out.push("no comp " + ' + js(comp_name) + ');',
             # the user's project is not changed: a colour-managed or > 8 bpc one only gets a warning
             aeapp.COLOR_CHECK_JSX, 'var cp = colorProblem(); if (cp) out.push("warn colour: " + cp);']
    ae_paths = {}
    for t in times:
        p = os.path.join(shot_dir, f"ae_{t:06.2f}.png")
        ae_paths[t] = p
        if os.path.exists(p):
            os.remove(p)
        lines.append(f'try {{ comp.saveFrameToPng({t}, new File({js(p)})); out.push("ok {t}"); }} catch (e) {{ out.push("fail {t} " + e); }}')
    done = os.path.join(shot_dir, "_shots.log")
    lines.append(f'var f = new File({js(done)}); f.open("w"); f.write(out.join("\\n")); f.close();')
    runner = os.path.join(out_dir, "_shots.jsx")
    open(runner, "w", encoding="utf-8").write("\n".join(lines))
    if os.path.exists(done):
        os.remove(done)
    osa(runner)
    env = {**os.environ, "RIVE_NO_TUI": "1"}
    rv_paths = {}
    for t in times:
        p = os.path.join(shot_dir, f"rive_{t:06.2f}.png")
        rv_paths[t] = p
        # + RIVE_SHOT_LAG: `rive --advance=T` shows the animation at T - 1/60 s (its first 60 Hz step only enters the
        # state machine) — measured: walk-test at T + 1/60 = AE at T to the pixel, a 300 px/s move at 0.25 s sits at 70 px
        subprocess.run(["rive", project_dir, f"--screenshot={p}", f"--artboard={artboard}", f"--advance={t + RIVE_SHOT_LAG:.4f}s", "--quiet"],
                       capture_output=True, text=True, env=env)
    try:                                                            # saveFrameToPng writes asynchronously
        aeapp.wait_settled(list(ae_paths.values()), timeout=900, watch=aeapp.DialogWatch())
    except RuntimeError as e:
        raise SystemExit(f"ae diff: {e}")
    if os.path.exists(done):
        for ln in open(done, encoding="utf-8", errors="replace").read().split("\n"):
            if ln.startswith("warn colour"):
                print(f"warning: the AE frames are not display values ({ln[13:]}): this comparison is not reliable; "
                      "set the project to 8 bpc without colour management (File > Project Settings)")
    from PIL import Image, ImageDraw
    tiles = []
    for t in times:
        if not (os.path.exists(ae_paths[t]) and os.path.exists(rv_paths[t])):
            print("missing", t)
            continue
        a = Image.open(ae_paths[t]).convert("RGB").resize((640, 360))
        b = Image.open(rv_paths[t]).convert("RGB").resize((640, 360))
        tile = Image.new("RGB", (1290, 380), (30, 30, 30))
        tile.paste(a, (0, 20))
        tile.paste(b, (650, 20))
        ImageDraw.Draw(tile).text((4, 4), f"t={t:.2f}s   AE | Rive", fill=(255, 255, 255))
        tiles.append(tile)
    outs = []
    for half in range(0, len(tiles), 6):
        tl = tiles[half:half + 6]
        sheet = Image.new("RGB", (1290, 380 * len(tl)))
        for i, x in enumerate(tl):
            sheet.paste(x, (0, 380 * i))
        out = os.path.join(shot_dir, f"compare_{half // 6}.jpg")
        sheet.save(out, quality=85)
        outs.append(out)
    return outs
