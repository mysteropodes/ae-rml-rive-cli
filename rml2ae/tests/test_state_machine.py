"""A Rive state machine with inputs -> an After Effects comp that plays it from keyable inputs (no After Effects):

  rml2ae: tests/cases/state_machine -> a controller null whose effect controls are the inputs (Checkbox for a bool and
          a trigger, Slider for a number), one precomp per (state, animation) above the base pose, and expressions:
          the controller replays the layer frame by frame, the precomps take their time and opacity from it.

The generated expressions run here in Node.js against a small After Effects mock: the inputs are keyed (hover on at
1 s and off at 2 s, press fired at 3 s, level 80 from 4 s) and the state that plays, its mix and the precomp
opacities are checked frame by frame against the Rive runtime's rules (state_machine_instance.cpp).

    python -m rml2ae.tests.test_state_machine
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
from rml2ae import statemachine as SMX      # noqa: E402

CASE = os.path.join(HERE, "cases", "state_machine")

MOCK = r"""
var SCHED = __SCHED__, DEF = __DEF__, FPS = 30;
function keyed(nm) { return { valueAtTime: function (t) { var v = DEF[nm], s = SCHED[nm] || [];
  for (var i = 0; i < s.length; i++) if (t >= s[i][0] - 1e-9) v = s[i][1]; return v; } }; }
var time = 0, CACHE = {};
function run(code, self) { var thisLayer = self, thisComp = COMP, source = { duration: self.srcDur || 0 }; return eval(code); }
var CTRL = { effect: function (nm) { return function (mn) {
  if (nm == "rive state" || nm == "rive mix") return { get value() { var k = nm + "@" + time;
    if (!(k in CACHE)) CACHE[k] = run(nm == "rive state" ? P3 : P2, CTRL); return CACHE[k]; } };
  return keyed(nm); }; } };
var COMP = { frameDuration: 1 / FPS, layer: function (nm) { return CTRL; } };
var P3 = __P3__, P2 = __P2__, LAYERS = __LAYERS__;
var out = [];
for (var f = 0; f <= 150; f++) {
  time = f / FPS;
  var st = CTRL.effect("rive state")("x").value, mx = CTRL.effect("rive mix")("x").value, row = [f, st[0], st[2], +mx[1].toFixed(3)], ops = [], tms = [];
  for (var i = 0; i < LAYERS.length; i++) {
    ops.push(+run(LAYERS[i].op, { srcDur: LAYERS[i].dur }).toFixed(2));
    tms.push(LAYERS[i].tr ? +run(LAYERS[i].tr, { srcDur: LAYERS[i].dur }).toFixed(3) : null);
  }
  out.push({ f: f, s: st[0], from: st[2], mix: +mx[1].toFixed(3), op: ops, tm: tms });
}
console.log(JSON.stringify(out));
"""


def _str_after(text, start):
    """the JS string literal that begins at text[start] (a double-quoted js() literal), decoded"""
    m = re.compile(r'"(?:[^"\\]|\\.)*"').match(text, start)
    return json.loads(m.group(0))


def check():
    tmp = tempfile.mkdtemp()
    try:
        conv = Converter(Project(CASE), os.path.join(tmp, "case"), replay=False)
        text = conv.convert()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    data, _notes = SMX.build(Project(CASE), Project(CASE).artboards[0])
    names = [s["name"] for s in data["states"]]
    assert names == ["Entry", "Exit", "Idle", "Hover", "Press", "Blend (level)"], names
    # the controller and its inputs, keyable
    assert 'smn' in text and '"State Machine \\u00b7 Button SM"' in text
    assert 'cb.name = "hover"; cb.property("ADBE Checkbox Control-0001").setValue(0);' in text
    assert 'cb.name = "press (trigger)"' in text
    assert re.search(r'slider\(smn\d+, "level", 50\);', text)
    # bottom first: the base pose, then one precomp per (state, animation)
    layers = re.findall(r'var (sml\d+) = smc\d+\.layers\.add\(c\d+\); \1\.name = "([^"]+)";', text)
    labels = [json.loads('"' + n + '"') for _v, n in layers]
    assert labels == ["Base pose", "Idle", "Hover", "Press", "Blend (level) · Left", "Blend (level) · Right"], labels
    durs = {"Base pose": 0, "Idle": 2.0, "Hover": 0.5, "Press": 1 / 3, "Blend (level) · Left": 1 / 30,
            "Blend (level) · Right": 1 / 30}
    specs = []
    for v, n in layers:
        lab = json.loads('"' + n + '"')
        i = text.index(f'tr({v}, "ADBE Opacity").expression = ') + len(f'tr({v}, "ADBE Opacity").expression = ')
        spec = {"op": _str_after(text, i), "dur": durs[lab], "tr": None}
        key = f'{v}.property("ADBE Time Remapping").expression = '
        if key in text:
            spec["tr"] = _str_after(text, text.index(key) + len(key))
        specs.append(spec)
    p3 = _str_after(text, text.index('p3.property("ADBE Point3D Control-0001").expression = ') + len('p3.property("ADBE Point3D Control-0001").expression = '))
    p2 = _str_after(text, text.index('p2.property("ADBE Point Control-0001").expression = ') + len('p2.property("ADBE Point Control-0001").expression = '))
    sched = {"hover": [[1.0, 1], [2.0, 0]], "press (trigger)": [[3.0, 1], [3.1, 0]], "level": [[4.0, 80]]}
    default = {"hover": 0, "press (trigger)": 0, "level": 50}
    prog = (MOCK.replace("__SCHED__", json.dumps(sched)).replace("__DEF__", json.dumps(default))
            .replace("__P3__", json.dumps(p3)).replace("__P2__", json.dumps(p2)).replace("__LAYERS__", json.dumps(specs)))
    node = shutil.which("node")
    if node is None:
        print("node not found: expressions not evaluated")
        return
    res = subprocess.run([node, "-"], input=prog, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    rows = json.loads(res.stdout)
    IDLE, HOVER, PRESS, BLEND = 2, 3, 4, 5
    at = {r["f"]: r for r in rows}
    # entry -> Idle at once; Idle loops (2 s), full weight
    assert at[0]["s"] == IDLE and at[0]["op"][1] == 100 and at[0]["tm"][1] == 0
    assert at[15]["tm"][1] == 0.5
    # hover on at 1 s: Hover, mixing in over 200 ms (6 frames) on top of Idle
    assert at[30]["s"] == HOVER and at[30]["from"] == IDLE and at[30]["mix"] == 0
    assert at[33]["mix"] == 0.5 and at[33]["op"][2] == 50 and at[33]["op"][1] == 100
    assert at[36]["mix"] == 1 and at[36]["op"][2] == 100 and at[36]["op"][1] == 0
    # Hover is one-shot (0.5 s): its time holds on the last frame
    assert at[50]["tm"][2] == round(0.5 - 1 / 60, 3), at[50]["tm"]
    # hover off at 2 s: back to Idle, which starts over
    assert at[60]["s"] == IDLE and at[60]["from"] == HOVER and at[60]["tm"][1] == 0
    # press fired at 3 s (Any State, 100 ms mix), back to Idle at the end of Press (exit time 100 %)
    assert at[90]["s"] == PRESS and at[93]["mix"] == 1
    assert at[99]["s"] == PRESS and at[100]["s"] == IDLE
    # the trigger is used once: it does not fire again while it stays on (3 s to 3.1 s)
    assert all(at[f]["s"] != PRESS for f in range(100, 120))
    # level 80 from 4 s: the blend state, halfway between Left (60) and Right (100)
    assert at[120]["s"] == BLEND
    assert at[121]["op"][4] == 100 and at[121]["op"][5] == 50, at[121]["op"]
    bad = [e for e in conv.rep.entries if e[1] == "unsupported"]
    assert not bad, bad


if __name__ == "__main__":
    check()
    print("state machine -> AE: ok")
