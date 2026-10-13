"""A film's shots -> a Montage comp, one comp per shot, each shot in its own folder (no After Effects):

  rml2ae: tests/cases/montage (three nested artboards cut by their holders' opacity, a title over the second one) ->
          three shots found, the title left out; --cuts replaces them, --no-montage drops the montage.
  JSX:    buildMontage / montageClear run in Node.js against a small After Effects project mock: each shot comp shows
          the main comp through its window under a Camera null, the Montage lays them end to end (shot 1 on top, a
          colour every other shot, a marker naming each), what one shot uses alone moves into its folder (Nested /
          Footage) and what two shots or the time between shots use stays put; a rebuild clears
          the previous montage and gives the same project.

    python -m rml2ae.tests.test_montage
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from rml2ae.convert import Converter, parse_cuts        # noqa: E402
from rml2ae.jsx import PRELUDE, MONTAGE_JSX             # noqa: E402
from rml2ae.model import Project                        # noqa: E402

CASE = os.path.join(HERE, "cases", "montage")

MOCK = r"""
var ITEMS = [], NEXT = 1;
function Item(name) { this.id = NEXT++; this.name = name; this.comment = ""; this.parentFolder = app.project.rootFolder; ITEMS.push(this); }
Item.prototype.remove = function () { ITEMS.splice(ITEMS.indexOf(this), 1); };
function FolderItem(name) { Item.call(this, name); }
FolderItem.prototype = Object.create(Item.prototype);
FolderItem.prototype.remove = function () {
  var self = this;
  ITEMS.filter(function (it) { return it.parentFolder === self; }).forEach(function (it) { it.remove(); });
  Item.prototype.remove.call(this);
};
function FootageItem(name) { Item.call(this, name); }
FootageItem.prototype = Object.create(Item.prototype);
function Layer(src, comp) {
  this.source = src; this.name = src ? src.name : "shape"; this.comp = comp; this._start = 0;
  this.inPoint = 0; this.outPoint = src && src.duration ? src.duration : comp.duration;
  this.timeRemapEnabled = false; this.stretch = 100; this.parent = null; this.label = 0; this.markers = [];
  var self = this;
  this.property = function (n) { if (n != "ADBE Marker") throw new Error("property " + n);
    return { setValueAtTime: function (t, m) { self.markers.push([t, m.comment]); } }; };
}
function MarkerValue(c) { this.comment = c; }
Object.defineProperty(Layer.prototype, "startTime", {
  get: function () { return this._start; },
  set: function (v) { var d = v - this._start; this._start = v; this.inPoint += d; this.outPoint += d; }
});
Layer.prototype.moveToEnd = function () { var L = this.comp._layers; L.splice(L.indexOf(this), 1); L.push(this); };
function CompItem(name, w, h, pa, dur, fps) {
  Item.call(this, name); this.width = w; this.height = h; this.pixelAspect = pa; this.duration = dur;
  this.frameDuration = 1 / fps; this.bgColor = [0, 0, 0]; this._layers = [];
  var self = this;
  this.layers = { add: function (src) { var L = new Layer(src, self); self._layers.unshift(L); return L; },
                  addNull: function (d) { var L = new Layer(null, self); L.outPoint = d; L.isNull = true; self._layers.unshift(L); return L; } };
}
CompItem.prototype = Object.create(Item.prototype);
Object.defineProperty(CompItem.prototype, "numLayers", { get: function () { return this._layers.length; } });
CompItem.prototype.layer = function (i) { return this._layers[i - 1]; };
var app = { project: { rootFolder: null, items: {
  addComp: function (n, w, h, pa, d, f) { return new CompItem(n, w, h, pa, d, f); },
  addFolder: function (n) { return new FolderItem(n); } } } };
app.project.rootFolder = { id: 0, name: "root" };
Object.defineProperty(app.project, "numItems", { get: function () { return ITEMS.length; } });
app.project.item = function (i) { return ITEMS[i - 1]; };
function log() {}
"""

SCENARIO = r"""
// the build: a film of 6 s at 25 fps cut in three shots; A plays nested comp A (its own image), B and C share an image,
// B nests a comp of its own; the background solid sits in AE's Solids folder; a title layer has no source
var ROOTF = folder(null, "film (rml2ae)"), FOOT = folder(ROOTF, "Footage"), SUBS = folder(ROOTF, "Nested");
var SOLIDS = folder(null, "Solids");
function foot(name, f) { var it = new FootageItem(name); it.parentFolder = f; return it; }
function comp(name, dur, f) { var c = app.project.items.addComp(name, 640, 360, 1, dur, 25); c.parentFolder = f; return c; }
var imgA = foot("a.png", FOOT), imgS = foot("shared.png", FOOT), bgS = foot("Background", SOLIDS);
var A = comp("Shot A", 2, SUBS), B = comp("Shot B", 2, SUBS), C = comp("Shot C", 2, SUBS), Bin = comp("B inner", 2, SUBS);
A.layers.add(imgA); B.layers.add(imgS); B.layers.add(Bin); C.layers.add(imgS);
var top = comp("Film", 6, ROOTF);
top.layers.add(bgS);
[[A, 0], [B, 2], [C, 4]].forEach(function (p) { var L = top.layers.add(p[0]); L.startTime = p[1]; });
var title = top.layers.add(null); title.inPoint = 2.4; title.outPoint = 3.2;
function where(it) { return it.parentFolder.name; }
function snapshot() {
  var mt = ITEMS.filter(function (it) { return it.comment == "rml2ae:montage"; });
  var shots = ITEMS.filter(function (it) { return it.comment && it.comment.indexOf("rml2ae:shot:") == 0; });
  return {
    montage: mt.map(function (m) { return { name: m.name, duration: m.duration, layers: m._layers.map(function (L) {
      return [L.name, L.startTime, L.inPoint, L.outPoint, L.label, L.markers]; }) }; }),
    shots: shots.map(function (s) {
      var film = s._layers.filter(function (L) { return L.source; })[0], cam = s._layers.filter(function (L) { return L.isNull; })[0];
      return { name: s.name, comment: s.comment, folder: where(s), parent: where(s.parentFolder), duration: s.duration, layers: s._layers.length,
               layer: [film.source.name, film.startTime], camera: [cam.name, cam.outPoint, film.parent === cam] }; }),
    folders: { imgA: where(imgA), imgS: where(imgS), bg: where(bgS), A: where(A), B: where(B), C: where(C), Bin: where(Bin), top: where(top),
               A_in: where(A.parentFolder), imgA_in: where(imgA.parentFolder), Bin_in: where(Bin.parentFolder) },
    items: ITEMS.length
  };
}
var out = {};
montageClear(ROOTF, SUBS, FOOT);
out.n = buildMontage(top, SHOTS, ROOTF, SUBS, FOOT);
out.first = snapshot();
montageClear(ROOTF, SUBS, FOOT);
out.cleared = snapshot();
out.n2 = buildMontage(top, SHOTS, ROOTF, SUBS, FOOT);
out.second = snapshot();
console.log(JSON.stringify(out));
"""


def convert(case=CASE, cuts=None, montage=True):
    out = tempfile.mkdtemp()
    conv = Converter(Project(case), out, replay=False, log=lambda *a: None)
    conv.montage = montage
    conv.cuts = cuts
    return conv, conv.convert()


def shots_in(text):
    m = re.search(r"buildMontage\(\w+, (\[.*?\]\]), ROOTF", text)
    return json.loads(m.group(1)) if m else None


def run_mock(shots):
    folder_fn = re.search(r"function folder\(parent, name\) \{.*?\n\}\n", PRELUDE, re.S).group(0)
    prog = MOCK + folder_fn + MONTAGE_JSX + f"var SHOTS = {json.dumps(shots)};\n" + SCENARIO
    res = subprocess.run([shutil.which("node"), "-"], input=prog, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def main():
    assert parse_cuts("0:Intro, 7.1:Chorus,12.5") == [(0.0, "Intro"), (7.1, "Chorus"), (12.5, "")]
    conv, text = convert()
    shots = shots_in(text)
    assert shots == [[0, 2, "Opening"], [2, 4, "Chase"], [4, 6, "Ending"]], shots      # the title is not a shot
    assert any("3 shots (from the opacity cuts)" in e[3] for e in conv.rep.entries), conv.rep.entries
    # the previous montage is cleared before the tidy pass, the new one built after it
    assert text.index("montageClear(ROOTF") < text.index("tidyProject(ROOTF)") < text.index("buildMontage(c")
    _, text = convert(cuts=parse_cuts("0:Intro,1.5:Middle,4"))
    assert shots_in(text) == [[0, None, "Intro"], [1.5, None, "Middle"], [4, None, ""]]
    _, text = convert(montage=False)
    assert shots_in(text) is None and "montageClear(ROOTF" in text
    # a project without shots (one artboard, a state machine) gets no montage
    _, text = convert(os.path.join(HERE, "cases", "state_machine"))
    assert shots_in(text) is None

    if shutil.which("node") is None:
        print("node not found: the montage script was not run")
        return
    out = run_mock(shots)
    assert out["n"] == 3 and out["n2"] == 3
    first = out["first"]
    mt = first["montage"][0]
    assert mt["name"] == "Film · Montage" and mt["duration"] == 6
    assert [L[0] for L in mt["layers"]] == ["Shot 01 · Opening", "Shot 02 · Chase", "Shot 03 · Ending"]
    assert [L[1] for L in mt["layers"]] == [0, 2, 4] and [L[3] - L[2] for L in mt["layers"]] == [2, 2, 2]
    assert [L[4] for L in mt["layers"]] == [9, 11, 9]                       # a colour every other shot
    assert all(L[5] == [[L[1], L[0]]] for L in mt["layers"]), mt["layers"]  # a marker naming the shot at its start
    for s, t0 in zip(first["shots"], (0, 2, 4)):
        assert s["folder"] == s["name"] and s["parent"] == "Shots" and s["duration"] == 2, s
        assert s["layer"] == ["Film", -t0], s                  # the main comp, seen through the shot's window
        assert s["camera"] == ["Camera", 2, True] and s["layers"] == 2, s   # under a Camera null
    f = first["folders"]
    assert (f["A"], f["A_in"]) == ("Nested", "Shot 01 · Opening"), f       # split as the build is
    assert (f["imgA"], f["imgA_in"]) == ("Footage", "Shot 01 · Opening"), f
    assert f["B"] == "Nested" and (f["Bin"], f["Bin_in"]) == ("Nested", "Shot 02 · Chase"), f
    assert f["C"] == "Nested", f
    assert f["imgS"] == "Footage" and f["bg"] == "Solids" and f["top"] == "film (rml2ae)", f   # shared / not ours
    cleared = out["cleared"]
    assert not cleared["montage"] and not cleared["shots"], cleared
    assert {k: v for k, v in cleared["folders"].items() if not k.endswith("_in")} == {
        "imgA": "Footage", "imgS": "Footage", "bg": "Solids", "A": "Nested", "B": "Nested", "C": "Nested", "Bin": "Nested",
        "top": "film (rml2ae)"}, cleared["folders"]
    assert cleared["folders"]["A_in"] == "film (rml2ae)", cleared["folders"]       # back in the build's own folders
    assert out["second"] == first, (out["second"], first)       # a rebuild gives the same project
    print("montage: ok")


if __name__ == "__main__":
    main()
