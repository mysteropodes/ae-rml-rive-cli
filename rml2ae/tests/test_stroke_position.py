"""Rive Stroke.position (inside / outside) <-> After Effects, both ways (no After Effects, no Rive CLI):

  rml2ae: tests/cases/stroke_position -> each paint of a Shape with an inside / outside stroke in its own vector group
          (a copy of the paths), the stroke's paths moved by Offset Paths of ±width/2 (keyed with the width), the sign
          following the path's winding; a centred stroke and the fill stay as they were
  ae2rml: a stroke whose last path operation is Offset Paths of ±width/2 on closed paths -> Stroke position, the
          Offset dropped from the stroke (fake AE items: py-aep cannot write an .aep)

    python -m rml2ae.tests.test_stroke_position
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from rml2ae.convert import Converter as RiveToAe, stroke_shift      # noqa: E402
from rml2ae.model import El, Project                                # noqa: E402

CASE = os.path.join(HERE, "cases", "stroke_position")


def check_rml2ae(tmp):
    conv = RiveToAe(Project(CASE), os.path.join(tmp, "case"), replay=False)
    text = conv.convert()
    amounts = re.findall(r'off\.property\("ADBE Vector Offset Amount"\)\.setValue\(([-\d.]+)\)', text)
    # Card inside 20 -> -10; Ring outside 10 (clockwise ellipse) -> +5; Wedge outside 12, counter-clockwise -> -6
    assert amounts == ["-10", "5", "-6"], amounts
    # the keyed width keys the offset too, at half the value
    assert 'keys(off.property("ADBE Vector Offset Amount"), [[0,5,"l",0,0,0,0],[50,15,"h",0,0,0,0]]);' in text
    # one group per paint, in AE order (Rive's last paint on top): Card = Inside, Fill; Wedge = Centre, Outside
    groups = re.findall(r'var pg = gcs\.addProperty\("ADBE Vector Group"\); pg\.name = "([^"]+)"', text)
    assert groups == ["Inside", "Fill", "Outside", "Fill", "Centre", "Outside"], groups
    # each paint group holds its own copy of the paths: the Card's rectangle twice
    assert text.count('addProperty("ADBE Vector Shape - Rect")') == 2
    # the round join of the Wedge's outside stroke is the Offset's join too
    assert 'off.property("ADBE Vector Offset Line Join").setValue(2);' in text
    bad = [e for e in conv.rep.entries if e[1] == "unsupported"]
    assert not bad, bad


def check_shift():
    sq = _shape
    assert stroke_shift(*sq("center", None)) == 0
    assert stroke_shift(*sq("inside", None)) == -0.5            # parametric paths run clockwise in AE
    assert stroke_shift(*sq("outside", None)) == 0.5
    ccw = [(0, 0), (0, 10), (10, 10), (10, 0)]
    assert stroke_shift(*sq("outside", ccw)) == -0.5
    assert stroke_shift(*sq("inside", ccw[::-1])) == -0.5
    assert stroke_shift(*sq("sideways", None)) == 0              # unknown value: centred, as the runtime default


def _shape(pos, verts):
    shape = El("Shape", {})
    if verts is None:
        p = El("Rectangle", {"width": "10", "height": "10"}, shape)
    else:
        p = El("PointsPath", {}, shape)
        p.children = [El("StraightVertex", {"x": str(x), "y": str(y)}, p) for x, y in verts]
    st = El("Stroke", {"position": pos}, shape)
    shape.children = [p, st]
    return st, shape


# ---------------------------------------------------------------- ae2rml, on fake AE items
class FakeProp:
    def __init__(self, mn, value=None, kids=()):
        self.match_name, self.value, self.properties = mn, value, list(kids)
        self.name = mn

    def property(self, mn):
        return next((p for p in self.properties if p.match_name == mn), None)


class FakeAProp:
    def __init__(self, p):
        self.p = p
        self.keys = p.value if isinstance(p.value, dict) else None
        self.animated = self.keys is not None
        self.value = None if self.animated else p.value

    def times(self):
        return sorted(self.keys) if self.animated else []

    def at(self, t):
        if not self.animated:
            return self.p.value
        ts = sorted(self.keys)
        if t <= ts[0]:
            return self.keys[ts[0]]
        for a, b in zip(ts, ts[1:]):
            if a <= t <= b:
                u = (t - a) / (b - a)
                return self.keys[a] + (self.keys[b] - self.keys[a]) * u
        return self.keys[ts[-1]]

    def static(self, dim=None, default=0.0):
        return default if self.value is None else self.value


def check_ae2rml():
    from rml2ae.ae2rml import shapes as S

    sb = S.ShapeBuilder.__new__(S.ShapeBuilder)
    sb.prop = lambda p, what: FakeAProp(p)

    def item(kind, mn, kids):
        return S.Item(kind, mn, kind, FakeProp(mn, kids=kids), 0)

    def rect(direction=1):
        return item("rect", "ADBE Vector Shape - Rect", [FakeProp("ADBE Vector Shape Direction", direction),
                                                          FakeProp("ADBE Vector Rect Size", [100, 60]),
                                                          FakeProp("ADBE Vector Rect Position", [0, 0]),
                                                          FakeProp("ADBE Vector Rect Roundness", 0)])

    def offset(amount, copies=1):
        return item("offset", "ADBE Vector Filter - Offset", [FakeProp("ADBE Vector Offset Amount", amount),
                                                              FakeProp("ADBE Vector Offset Copies", copies)])

    def pos(m, width, path=None):
        pr = S.PathRef(path or rect(), (), "c.0")
        return sb._stroke_position(m, [(pr, 0)], FakeAProp(FakeProp("w", width)))
    assert pos(offset(-5), 10) == "inside"
    assert pos(offset(5), 10) == "outside"
    assert pos(offset(5), 10, rect(3)) == "inside"             # a reversed rectangle: positive moves it inwards
    assert pos(offset(4), 10) is None                           # not half the width: a real Offset Paths
    assert pos(offset(5, copies=2), 10) is None
    assert pos(offset({0.0: 5, 1.0: 15}), {0.0: 10, 1.0: 30}) == "outside"     # keyed together
    assert pos(offset({0.0: 5, 1.0: 5}), {0.0: 10, 1.0: 30}) is None
    zig = item("zig zag", "ADBE Vector Filter - Zigzag", [])
    assert pos(zig, 10) is None
    # an open path has no inside
    opened = item("path", "ADBE Vector Shape - Group", [FakeProp("ADBE Vector Shape Direction", 1)])
    sb.path_fn = lambda it, d, _orig=S.ShapeBuilder.path_fn: (
        (lambda t: ([(0, 0), (10, 0), (10, 10)], [(0, 0)] * 3, [(0, 0)] * 3, False)), []) \
        if it is opened else _orig(sb, it, d)
    assert pos(offset(5), 10, opened) is None


def main():
    tmp = tempfile.mkdtemp(prefix="stroke_position_")
    check_shift()
    check_rml2ae(tmp)
    check_ae2rml()
    print("test_stroke_position: ok (rml2ae inside / outside / keyed / winding, ae2rml Offset Paths -> position)")


if __name__ == "__main__":
    main()
