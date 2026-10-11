"""Luau script inputs <-> After Effects effect controls (no After Effects, no Rive CLI):

  rml2ae: tests/cases/script_inputs -> the scripted element's layer carries 'Luau: <name>' controls (Slider for a
          number, keyed like the Rive input; Checkbox for a boolean; Color Control for a colour)
  ae pull: the controls' values (as the pull dump reads them) -> the inputs' propertyValue in scene.rml, colour in the
          RML's own spelling; a keyed control is left alone
  replay: the replay cache is keyed by the isolated scene and the scripts, so a pulled value renders again

    python -m rml2ae.tests.test_script_inputs
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from rml2ae.convert import Converter, script_color     # noqa: E402
from rml2ae.model import Project                       # noqa: E402
from rml2ae import pull as PULL                        # noqa: E402

CASE = os.path.join(HERE, "cases", "script_inputs")


def check_build(tmp):
    conv = Converter(Project(CASE), os.path.join(tmp, "build"), replay=False)
    text = conv.convert()
    assert re.search(r'var si = slider\(\w+, "Luau: count", 12\);', text), "number input -> Slider"
    assert re.search(r'keys\(si, \[\[0,12,"l",[^\]]*\],\[30,24,', text), "the keyed input keys the Slider"
    assert 'sb.name = "Luau: spin"; sb.property("ADBE Checkbox Control-0001").setValue(1);' in text
    m = re.search(r'sc\.name = "Luau: tint"; sc\.property\("ADBE Color Control-0001"\)\.setValue\(\[([^\]]+)\]\);', text)
    assert m and [round(float(x), 3) for x in m.group(1).split(",")] == [1.0, 0.784, 0.0], m and m.group(1)
    assert any("script inputs as effect controls" in e[-1] for e in conv.rep.entries)


def check_pull(tmp):
    d = os.path.join(tmp, "proj")
    shutil.copytree(CASE, d)
    proj = Project(d)
    files = {p: PULL.RmlText(p) for p in [os.path.join(d, "scene.rml")]}
    f = files[os.path.join(d, "scene.rml")]
    comps = [{"tag": "rive:0:1|0:20", "name": "Sparkles", "layers": [{"id": "0:10", "luau": [
        {"name": "count", "k": "ADBE Slider Control", "keyed": True, "v": 30},
        {"name": "spin", "k": "ADBE Checkbox Control", "keyed": False, "v": 0},
        {"name": "tint", "k": "ADBE Color Control", "keyed": False, "v": [0.0, 0.5, 1.0]}]}]}]
    changes = []
    PULL.script_inputs(proj, comps, lambda oid: f if f.has_id(oid) else None, changes)
    f.save()
    text = open(os.path.join(d, "scene.rml"), encoding="utf-8").read()
    assert '<ScriptInputNumber propertyValue="12" name="count" id="0:11"/>' in text, "a keyed control stays in the animation"
    assert re.search(r'<ScriptInputBoolean propertyValue="false" name="spin"/>', text), text
    assert re.search(r'<ScriptInputColor propertyValue="FF0080FF" name="tint"/>', text), text
    assert len(changes) == 2, changes
    # unchanged values: nothing to write
    changes = []
    PULL.script_inputs(Project(d), [{"tag": "rive:0:1|0:20", "name": "S", "layers": [{"id": "0:10", "luau": [
        {"name": "spin", "k": "ADBE Checkbox Control", "keyed": False, "v": 0}]}]}], lambda oid: f, changes)
    assert not changes, changes


def check_color():
    assert script_color("FFFFC800")[:3] == (1.0, 200 / 255, 0.0)
    assert script_color(str(-14336))[:3] == (1.0, 200 / 255, 0.0)       # 0xFFFFC800 as a signed int


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        check_build(tmp)
        check_pull(tmp)
        check_color()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("script inputs <-> AE controls: ok")
