"""Render the After Effects references listed in spec.py (After Effects must be open).

    python render_refs.py [slug ...] [--all]

Uses its own project, fxref.aep, in this folder (created on first run from src/src.png and src/map.png, never committed).
It refuses to run if another project with content is open. Renders in batches of 8 saveFrameToPng per script (After
Effects' UI freezes beyond about 10 per script) and skips references already rendered unless --all.
Set AE_APP to your After Effects application name if it is not "Adobe After Effects 2026" (macOS, AppleScript).
"""
import json
import os
import subprocess
import sys
import time

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
from spec import renders, NEEDS_MAP  # noqa: E402

AE_APP = os.environ.get("AE_APP", "Adobe After Effects 2026")
PROJECT = os.path.join(D, "fxref.aep")


def js(v):
    return json.dumps(v)


def batch_jsx(rs, log):
    L = ['var out = [];', 'try {',
         f'var proj = new File({js(PROJECT)});',
         'var cur = (app.project && app.project.file) ? app.project.file.fsName : "";',
         'if (cur != proj.fsName) {',
         '  if (app.project && (app.project.file || app.project.numItems > 0)) throw new Error("another project is open: close it first (" + cur + ")");',
         '  if (proj.exists) app.open(proj);',
         f'  else {{ app.newProject(); app.project.importFile(new ImportOptions(new File({js(os.path.join(D, "src", "src.png"))}))); app.project.save(proj); }}',
         '}',
         'function item(n) { for (var i = 1; i <= app.project.numItems; i++) if (app.project.item(i).name == n) return app.project.item(i); return null; }',
         'var foot = item("src.png"); var mapf = item("map.png");',
         f'if (!mapf) mapf = app.project.importFile(new ImportOptions(new File({js(os.path.join(D, "src", "map.png"))})));',
         'try { app.disableRendering = false; } catch (e0) {}']
    for r in rs:
        name = f"{r['slug']}_{r['k']}"
        png = os.path.join(D, r["png"])
        L.append('try {')
        L.append(f'  var old = item({js(name)}); if (old) old.remove();')
        L.append(f'  var c = app.project.items.addComp({js(name)}, 640, 360, 1, 1, 30); c.bgColor = [0, 0, 0];')
        if r["slug"] in NEEDS_MAP:
            L.append('  var ml = c.layers.add(mapf); ml.enabled = false;')
        L.append('  var Ly = c.layers.add(foot); Ly.moveToBeginning();')
        if r["mn"]:
            L.append(f'  var fx = Ly.property("ADBE Effect Parade").addProperty({js(r["mn"])});')
            vals = dict(r["vals"])
            for i, v in NEEDS_MAP.get(r["slug"], {}).items():
                vals.setdefault(str(i), v)
            for i, v in vals.items():
                L.append(f'  fx.property({int(i)}).setValue({js(v)});')
        L.append(f'  c.saveFrameToPng(0, new File({js(png)})); out.push("ok {name}");')
        # e.message, never the Error object itself: concatenating it can open a modal error in After Effects
        L.append(f'}} catch (e) {{ out.push("FAIL {name} " + (e && e.message ? e.message : "error") + " line " + (e && e.line)); }}')
    L.append('app.project.save();')
    L.append('} catch (eTop) { out.push("TOP " + (eTop && eTop.message ? eTop.message : "error")); }')
    L.append(f'var f = new File({js(log)}); f.open("w"); f.write(out.join("\\n")); f.close();')
    return "\n".join(L)


def main():
    rs = renders()
    only = {a for a in sys.argv[1:] if not a.startswith("--")}
    if only:
        rs = [r for r in rs if r["slug"] in only]
    if "--all" not in sys.argv:
        rs = [r for r in rs if not os.path.exists(os.path.join(D, r["png"]))]
    tmp = os.path.join(D, "_render")
    os.makedirs(tmp, exist_ok=True)
    for b in range(0, len(rs), 8):
        chunk = rs[b:b + 8]
        log, jsx = os.path.join(tmp, f"batch_{b // 8}.log"), os.path.join(tmp, f"batch_{b // 8}.jsx")
        open(jsx, "w").write(batch_jsx(chunk, log))
        if os.path.exists(log):
            os.remove(log)
        subprocess.Popen(["osascript", "-e", f'tell application "{AE_APP}" to DoScriptFile "{jsx}"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < 600 and not os.path.exists(log):
            time.sleep(1)
        txt = open(log).read() if os.path.exists(log) else "timeout"
        if txt.startswith("TOP") or txt == "timeout":
            print("stopped:", txt[:300])
            return 1
        pngs = [os.path.join(D, r["png"]) for r in chunk]
        t1 = time.time()                      # saveFrameToPng is asynchronous: wait for every PNG to stop growing
        while time.time() - t1 < 300:
            sizes = [os.path.getsize(p) if os.path.exists(p) else -1 for p in pngs]
            if all(s > 0 for s in sizes):
                time.sleep(1.5)
                if sizes == [os.path.getsize(p) for p in pngs]:
                    break
            time.sleep(1)
        fails = [ln for ln in txt.split("\n") if not ln.startswith("ok")]
        print(f"batch {b // 8}: {len(chunk)} renders, {time.time() - t0:.0f}s", "; ".join(fails)[:600], flush=True)


if __name__ == "__main__":
    sys.exit(main())
