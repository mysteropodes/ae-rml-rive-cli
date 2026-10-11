"""Rive flex layouts -> After Effects layers placed by expressions (no After Effects):

  rml2ae: tests/cases/layout -> a 'Layout · Page' null with a slider per size, gap and padding; each LayoutComponent a
          null whose position expression solves the layout again (rml2ae/layout.py), its fill on a rectangle whose
          size comes from the same solve.

The solver's places are checked against the Rive runtime by CI (tools/layout_check.py: screenshots of the case and of
its flattened copy). Here the generated expressions run in Node.js against a small After Effects mock: with the
sliders at their Rive values they give the solver's places, and editing a slider (a wider logo, a larger gap) moves
the boxes that follow it. A layout outside the solved subset keeps the CLI replay.

    python -m rml2ae.tests.test_layout
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
from rml2ae.convert import Converter        # noqa: E402
from rml2ae.model import Project            # noqa: E402
from rml2ae import layout as LAY            # noqa: E402

CASE = os.path.join(HERE, "cases", "layout")

# where the Rive runtime puts them (CI compares the runtime's screenshot with these places): left, top, width, height
EXPECTED = {
    "Header": [40, 40, 720, 80], "Logo": [10, 10, 60, 60], "Title": [80, 19.5, 520, 41], "Button": [610, 20, 100, 40],
    "Cards": [40, 140, 720, 200], "Card 1": [0, 0, 150, 200], "Card 2": [290, 40, 150, 120], "Card 3": [580, 55, 140, 90],
    "Line 1": [50, 10, 80, 30], "Line 2": [10, 50, 120, 30],
    "Footer": [40, 360, 720, 60], "F1": [660, 5, 50, 50], "F2": [570, 5, 80, 30],
    "Stack": [40, 440, 200, 150], "S1": [90, 70, 100, 30], "S2": [130, 20, 60, 40],
}

MOCK = r"""
var SL = __SLIDERS__, OUT = {};
var CTRL = { effect: function (n) { if (!(n in SL)) throw new Error("no effect " + n);
  return function (i) { return { value: SL[n] }; }; } };
var thisComp = { layer: function (nm) { if (nm != __CTRL__) throw new Error("no layer " + nm); return CTRL; } };
var EX = __EXPRS__;
for (var k in EX) { var value = EX[k][1]; OUT[k] = eval(EX[k][0]); }
console.log(JSON.stringify(OUT));
"""


def _str_after(text, start):
    m = re.compile(r'"(?:[^"\\]|\\.)*"').match(text, start)
    return json.loads(m.group(0))


def expressions(text):
    """{layer name: {"x": expr, "y": expr, "size": expr}} from the generated JSX, with each layer's static x / y"""
    out = {}
    for m in re.finditer(r'var (nul\d+) = \w+\.layers\.addNull\(\); \1\.name = ("(?:[^"\\]|\\.)*");', text):
        var, name = m.group(1), json.loads(m.group(2))
        e = {}
        for axis, key in (("x", "Position_0"), ("y", "Position_1")):
            k = f'tr({var}, "ADBE {key}").expression = '
            i = text.find(k, m.end())
            if i < 0:
                break
            e[axis] = _str_after(text, i + len(k))
        if len(e) < 2:
            continue
        pos = re.search(re.escape(f'tr({var}, "ADBE Position").setValue([') + r'([-\d.e]+), ([-\d.e]+)\]', text[m.end():])
        e["x0"], e["y0"] = float(pos.group(1)), float(pos.group(2))
        nxt = text.find("var nul", m.end())
        box = text.find(f'lyb.parent = {var};', m.end(), nxt if nxt > 0 else len(text))
        if box > 0:
            k = 'lyr.property("ADBE Vector Rect Size").expression = '
            e["size"] = _str_after(text, text.index(k, box) + len(k))
        out[name] = e
    return out


def run(exprs, sliders):
    node = shutil.which("node")
    flat = {}
    for name, e in exprs.items():
        flat[name + "|x"] = [e["x"], e["x0"]]
        flat[name + "|y"] = [e["y"], e["y0"]]
        if "size" in e:
            flat[name + "|size"] = [e["size"], [0, 0]]
    prog = (MOCK.replace("__SLIDERS__", json.dumps(sliders)).replace("__CTRL__", json.dumps("Layout · Page"))
            .replace("__EXPRS__", json.dumps(flat)))
    res = subprocess.run([node, "-"], input=prog, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def convert(case, **kw):
    tmp = tempfile.mkdtemp()
    try:
        conv = Converter(Project(case), os.path.join(tmp, "case"), replay=False, **kw)
        return conv, conv.convert()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check():
    proj = Project(CASE)
    ab = proj.artboards[0]
    T, index, why = LAY.tree(proj, ab)
    assert why is None, why
    conv, text = convert(CASE)
    sliders = {json.loads(m.group(1)): float(m.group(2))
               for m in re.finditer(r'slider\(LYC, ("(?:[^"\\]|\\.)*"), ([-\d.e]+)\);', text)}
    assert sliders["Header · Gap"] == 10 and sliders["Page · Padding Top"] == 40, sliders
    assert "Title · Width" not in sliders                   # fill: sized by the layout, no slider
    assert "Card 3 · Width" not in sliders and sliders["Card 3 · Gap"] == 5     # hug
    exprs = expressions(text)
    assert sorted(exprs) == sorted(EXPECTED), sorted(exprs)
    # a parametric path in a styled layout takes the layout's size (Rive's controlSize): the button's dot, card 3's frame
    assert text.count('"ADBE Vector Ellipse Size").expression') == 1 and text.count('"ADBE Vector Rect Size").expression') >= 2
    bad = [e for e in conv.rep.entries if e[1] == "unsupported"]
    assert not bad, bad
    if shutil.which("node") is None:
        print("node not found: expressions not evaluated")
        return
    R = LAY.solve(T)
    for e in LAY.layout_nodes(ab):
        assert [round(v, 3) for v in R[index[id(e)]]] == EXPECTED[e.name], (e.name, R[index[id(e)]])
    out = run(exprs, sliders)
    for name, (l, t, w, h) in EXPECTED.items():
        x0, y0 = exprs[name]["x0"], exprs[name]["y0"]
        assert abs(out[name + "|x"] - (x0 + l)) < 1e-6 and abs(out[name + "|y"] - (y0 + t)) < 1e-6, (name, out[name + "|x"], out[name + "|y"])
        if name + "|size" in out:
            assert out[name + "|size"] == [w, h], (name, out[name + "|size"])
    assert exprs["Button"]["x0"] == 5 and exprs["Button"]["y0"] == -5      # Rive's x / y stay on top of the layout
    # edits in AE: a wider logo shrinks the title (it fills the rest), a taller card 1 makes the row taller and pushes
    # the footer down; a larger padding on Card 3 widens it (hug)
    sliders2 = dict(sliders, **{"Logo · Width": 100, "Card 1 · Height": 240, "Card 3 · Padding Left": 30})
    out2 = run(exprs, sliders2)
    assert out2["Title|x"] == 120 and out2["Title|size"] == [480, 41], (out2["Title|x"], out2["Title|size"])
    assert out2["Cards|size"] == [720, 240] and out2["Footer|y"] == 400, (out2["Cards|size"], out2["Footer|y"])
    assert out2["Card 3|size"] == [160, 90], out2["Card 3|size"]
    # outside the solved subset (wrap): the CLI replay stays, as before
    tmp = tempfile.mkdtemp()
    try:
        case = os.path.join(tmp, "wrap")
        shutil.copytree(CASE, case)
        p = os.path.join(case, "scene.rml")
        src = open(p).read()
        open(p, "w").write(src.replace('name="Cards style"', 'flexWrapValue="wrap" name="Cards style"'))
        conv2, text2 = convert(case)
        assert "LYC" not in text2
        assert any("flexWrapValue=wrap" in e[3] for e in conv2.rep.entries), conv2.rep.entries[:5]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    check()
    print("layout -> AE expressions: ok")
