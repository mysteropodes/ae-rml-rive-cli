// Rive — dockable After Effects panel: rebuild the open project from a Rive CLI project (scene.rml) with one click.
// Install (once, admin rights): copy this file into
//   macOS:   /Applications/Adobe After Effects 2026/Scripts/ScriptUI Panels/                       then Window > Rive.jsx
//   Windows: C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\Scripts\ScriptUI Panels\
// The panel runs rml2ae (Python) to write the .jsx, then evaluates it inside AE (no AppleScript, no terminal).
(function (thisObj) {
  var ROOT = "";                                   // repository root (set by the generator of this file)
  var WIN = $.os.indexOf("Windows") >= 0;
  var PY = ROOT + (WIN ? "\\.venv\\Scripts\\python.exe" : "/.venv/bin/python");
  var SETTINGS = "rml2ae";
  function pref(k, d) { return app.settings.haveSetting(SETTINGS, k) ? app.settings.getSetting(SETTINGS, k) : d; }
  function setPref(k, v) { app.settings.saveSetting(SETTINGS, k, v); }
  // macOS: a login bash; Windows: cmd.exe (paths in double quotes, which Windows file names cannot contain)
  function sh(cmd) { return WIN ? system.callSystem("cmd.exe /c \"" + cmd + "\"") : system.callSystem("/bin/bash -lc " + quote(cmd)); }
  function quote(s) { return WIN ? "\"" + s + "\"" : "'" + s.replace(/'/g, "'\\''") + "'"; }
  function cdRoot() { return (WIN ? "cd /d " : "cd ") + quote(ROOT); }
  function lastLines(s, n) { var l = String(s).replace(/\s+$/, "").split(/\r?\n/); return l.slice(Math.max(0, l.length - n)).join("\n"); }
  function basename(p) { var m = p.match(/([^\/\\]+)[\/\\]?$/); return m ? m[1] : p; }
  function plog(s) { try { var d = projectDir(); if (!d) return; var f = new File(d + "/build/rml2ae/panel.log"); f.open("a"); f.write(new Date().toTimeString().substr(0, 8) + " " + s + "\n"); f.close(); } catch (e) {} }

  var win = (thisObj instanceof Panel) ? thisObj : new Window("palette", "Rive", undefined, { resizeable: true });
  win.orientation = "column"; win.alignChildren = ["fill", "top"]; win.spacing = 6; win.margins = 8;
  var row = win.add("group"); row.orientation = "row"; row.alignChildren = ["fill", "center"];
  var dirTxt = row.add("edittext", undefined, pref("project", "")); dirTxt.characters = 28;
  var pick = row.add("button", undefined, "…"); pick.preferredSize.width = 28;
  var opts = win.add("group"); opts.orientation = "row";
  var replayCb = opts.add("checkbox", undefined, "Luau replay"); replayCb.value = pref("replay", "1") == "1";
  var mainTxt = opts.add("edittext", undefined, pref("main", "")); mainTxt.characters = 12; mainTxt.helpTip = "artboard (empty = default)";
  var go = win.add("button", undefined, "Update from RML \u2192 this project (incremental)");
  var pullBtn = win.add("button", undefined, "Pull this project\u2019s edits \u2192 RML");
  var row2 = win.add("group"); row2.orientation = "row"; row2.alignChildren = ["fill", "center"];
  var viewer = row2.add("button", undefined, "Rive viewer"); var report = row2.add("button", undefined, "Report");
  var status = win.add("statictext", undefined, "ready", { multiline: true }); status.preferredSize.height = 90;

  function say(s) { status.text = s; try { if (win.update) win.update(); } catch (e) {} try { app.refresh(); } catch (e2) {} }   // a docked Panel has no update(): never let the status kill the handler
  function guarded(fn) { return function () { try { fn(); } catch (e) { say("error: " + e.toString() + " (line " + e.line + ")"); plog("error: " + e.toString() + " line " + e.line); } }; }
  function projectDir() { return dirTxt.text.replace(/[\/\\]+$/, ""); }
  pick.onClick = function () { var f = Folder.selectDialog("Rive CLI project (folder with scene.rml)"); if (f) { dirTxt.text = f.fsName; setPref("project", f.fsName); } };
  dirTxt.onChange = function () { setPref("project", dirTxt.text); };
  replayCb.onClick = function () { setPref("replay", replayCb.value ? "1" : "0"); };
  mainTxt.onChange = function () { setPref("main", mainTxt.text); };

  go.onClick = guarded(function () {
    var dir = projectDir();
    if (!dir || !File(dir + "/scene.rml").exists) { say("no scene.rml in " + dir); return; }
    if (!app.project) { say("open or create a project first"); return; }
    var name = basename(dir);
    var jsx = dir + "/build/rml2ae/" + name + ".jsx";
    say("generating\u2026 (rml2ae" + (replayCb.value ? ", Luau replay on: the Rive CLI renders what AE cannot draw, minutes on a first run" : "") + ")");
    var t0 = new Date().getTime();
    var cmd = cdRoot() + " && " + quote(PY) + " -m rml2ae " + quote(dir) + " --incremental" + (replayCb.value ? "" : " --no-replay") + (mainTxt.text ? " --main " + quote(mainTxt.text) : "") + " 2>&1";
    var out = lastLines(sh(cmd), 4);
    plog("update: " + cmd + "\n" + out);
    var fresh = File(jsx).exists && File(jsx).modified.getTime() >= t0 - 2000;
    if (!fresh) { say("generation failed (" + Math.round((new Date().getTime() - t0) / 1000) + " s):\n" + out); return; }
    say("building in After Effects\u2026 (" + Math.round((new Date().getTime() - t0) / 1000) + " s to generate)");
    try { $.evalFile(File(jsx)); } catch (e) { say("script error: " + e.toString()); return; }
    var log = File(dir + "/build/rml2ae/" + name + ".ae.log"); var txt = "";
    if (log.exists) { log.open("r"); txt = log.read(); log.close(); }
    var failed = txt.match(/FAILED[^\n]*/g);
    say((failed ? failed.length + " element(s) failed — see the log" : "done") + " in " + Math.round((new Date().getTime() - t0) / 1000) + " s");
  });
  pullBtn.onClick = guarded(function () {
    var dir = projectDir();
    if (!dir || !File(dir + "/scene.rml").exists) { say("no scene.rml in " + dir); return; }
    say("reading the project\u2026");
    var base = cdRoot() + " && " + quote(PY) + " -m rml2ae.ae pull " + quote(dir);
    var jsx = lastLines(sh(base + " --prepare 2>&1"), 1);
    if (!File(jsx).exists) { say("cannot prepare the pull:\n" + jsx); return; }
    try { $.evalFile(File(jsx)); } catch (e) { say("dump error: " + e.toString()); return; }
    var out = sh(base + " --apply 2>&1");
    plog("pull: " + out);
    say(out.replace(/\s+$/, ""));
  });
  viewer.onClick = guarded(function () {
    var dir = projectDir(); if (!dir) { say("choose a project"); return; }
    if (WIN) {   // a .cmd file opened like a double-click: its own console window, the panel does not wait
      var w = File(dir + "/build/rml2ae/viewer.cmd"); w.open("w"); w.write("@cd /d " + quote(File(dir).fsName) + "\r\nrive .\r\n"); w.close();
      w.execute();
      say("Rive viewer launched in a console window");
      return;
    }
    var f = File(dir + "/build/rml2ae/viewer.command"); f.open("w"); f.write("#!/bin/bash\ncd " + quote(dir) + " && rive .\n"); f.close();
    sh("chmod +x " + quote(f.fsName) + " && open -a Terminal " + quote(f.fsName));
    say("Rive viewer launched in Terminal");
  });
  report.onClick = guarded(function () { var dir = projectDir(); var f = File(dir + "/build/rml2ae/" + basename(dir) + ".ae-report.md"); if (f.exists) f.execute(); else say("no report yet"); });

  if (win instanceof Window) { win.center(); win.show(); } else { win.layout.layout(true); }
})(this);
