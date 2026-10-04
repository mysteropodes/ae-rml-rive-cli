"""Render the After Effects references listed in spec.py (After Effects must be open).

    python render_refs.py [slug ...] [--all]
    python render_refs.py --params [slug ...]     # record the effects' parameter lists into ../fxlib/_ae_params.json

Uses its own project, fxref.aep, in this folder (created on first run from src/src.png and src/map.png, never committed).
It refuses to run if another project with content is open. Renders in batches of 8 saveFrameToPng per script (After
Effects' UI freezes beyond about 10 per script) and skips references already rendered unless --all.
--params adds each effect once to a scratch comp and records, per parameter, its position i (the manifests' "ae"),
match name, name (in the After Effects UI language), value type, default value and range; effects already in
_ae_params.json are kept unless named. Set AE_APP to your After Effects application name if it is not "Adobe After Effects 2026" (macOS, AppleScript).
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


# ExtendScript has no JSON object: a small serializer for numbers, strings, arrays and null
JSX_JSON = (
    'function jq(v) {'
    ' if (v === null || v === undefined) return "null";'
    ' if (typeof v == "number") return isFinite(v) ? String(v) : "null";'
    ' if (typeof v == "boolean") return v ? "true" : "false";'
    ' if (typeof v == "string") return \'"\' + v.replace(/\\\\/g, "\\\\\\\\").replace(/"/g, \'\\\\"\') + \'"\';'
    ' if (v instanceof Array) { var a = []; for (var k = 0; k < v.length; k++) a.push(jq(v[k])); return "[" + a.join(",") + "]"; }'
    ' return "null"; }')


def open_project_jsx():
    return ['try {',
            f'var proj = new File({js(PROJECT)});',
            'var cur = (app.project && app.project.file) ? app.project.file.fsName : "";',
            'if (cur != proj.fsName) {',
            '  if (app.project && (app.project.file || app.project.numItems > 0)) throw new Error("another project is open: close it first (" + cur + ")");',
            '  if (proj.exists) app.open(proj);',
            f'  else {{ app.newProject(); app.project.importFile(new ImportOptions(new File({js(os.path.join(D, "src", "src.png"))}))); app.project.save(proj); }}',
            '}',
            'function item(n) { for (var i = 1; i <= app.project.numItems; i++) if (app.project.item(i).name == n) return app.project.item(i); return null; }']


def params_jsx(mns, log, out_json):
    """One script: each effect added to a scratch solid, its parameter list written as JSON lines to out_json."""
    L = ['var out = []; var rows = [];', JSX_JSON] + open_project_jsx()
    L += ['var c = item("_params") || app.project.items.addComp("_params", 64, 64, 1, 1, 30);',
          'var sl = c.layers.addSolid([0.5, 0.5, 0.5], "probe", 64, 64, 1);']
    for mn in mns:
        L += ['try {',
              f'  var fx = sl.property("ADBE Effect Parade").addProperty({js(mn)}); var ps = [];',
              '  for (var i = 1; i <= fx.numProperties; i++) {',
              '    var p = fx.property(i); var t = null, d = null, lo = null, hi = null;',
              '    if (p.propertyType == PropertyType.PROPERTY) {',
              '      t = String(p.propertyValueType);',
              '      try { if (p.propertyValueType != PropertyValueType.NO_VALUE && p.propertyValueType != PropertyValueType.CUSTOM_VALUE) d = p.value; } catch (e1) {}',
              '      try { if (p.hasMin) lo = p.minValue; if (p.hasMax) hi = p.maxValue; } catch (e2) {}',
              '    }',
              '    ps.push("{\\"i\\":" + i + ",\\"mn\\":" + jq(p.matchName) + ",\\"name\\":" + jq(p.name) + ",\\"type\\":" + jq(t) + ",\\"default\\":" + jq(d) + ",\\"min\\":" + jq(lo) + ",\\"max\\":" + jq(hi) + "}");',
              '  }',
              f'  rows.push("{{\\"mn\\":" + jq({js(mn)}) + ",\\"name\\":" + jq(fx.name) + ",\\"params\\":[" + ps.join(",") + "]}}"); out.push("ok " + {js(mn)});',
              '  fx.remove();',
              f'}} catch (e) {{ out.push("FAIL " + {js(mn)} + " " + (e && e.message ? e.message : "error")); }}']
    L += ['sl.remove();',
          '} catch (eTop) { out.push("TOP " + (eTop && eTop.message ? eTop.message : "error")); }',
          f'var g = new File({js(out_json)}); g.encoding = "UTF-8"; g.open("w"); g.write(rows.join("\\n")); g.close();',
          f'var f = new File({js(log)}); f.open("w"); f.write(out.join("\\n")); f.close();']
    return "\n".join(L)


def record_params(slugs):
    lib = os.path.join(os.path.dirname(D), "fxlib")
    spec = {r["slug"]: r["mn"] for r in renders() if r["mn"]}
    mns = sorted({spec[s] for s in (slugs or spec) if s in spec})
    tmp = os.path.join(D, "_render")
    os.makedirs(tmp, exist_ok=True)
    log, jsx, rows = (os.path.join(tmp, n) for n in ("params.log", "params.jsx", "params.jsonl"))
    for f in (log, rows):
        if os.path.exists(f):
            os.remove(f)
    open(jsx, "w").write(params_jsx(mns, log, rows))
    run_jsx(jsx, log)
    txt = open(log).read() if os.path.exists(log) else "timeout"
    print(txt)
    if not os.path.exists(rows):
        return 1
    path = os.path.join(lib, "_ae_params.json")
    known = json.load(open(path, encoding="utf-8"))
    for line in open(rows, encoding="utf-8").read().splitlines():
        r = json.loads(line)
        known[r["mn"]] = {"name": r["name"], "params": r["params"]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(known.items())), f, ensure_ascii=False)
    print(f"{path}: {len(known)} effects")
    return 0


def run_jsx(jsx, log, timeout=600):
    subprocess.Popen(["osascript", "-e", f'tell application "{AE_APP}" to DoScriptFile "{jsx}"'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < timeout and not os.path.exists(log):
        time.sleep(1)


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
    if "--params" in sys.argv:
        return record_params([a for a in sys.argv[1:] if not a.startswith("--")])
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
