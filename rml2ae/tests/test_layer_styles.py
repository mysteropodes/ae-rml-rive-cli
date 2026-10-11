"""Layer Styles, Curves and the Stroke effect -> Rive (no After Effects, no Rive CLI):

  examples/demo.aep, edited in memory: the Card shape layer gets every Layer Style on, the Title text layer a Stroke
  and a Drop Shadow; the Card gets an open mask with a Stroke effect and a Curves effect (both effects are stand-ins:
  py-aep cannot add an effect the project has never used). Checks the copies ae2rml writes, their place in the drawing
  order, then reads the result back with rml2ae.

    python -m rml2ae.tests.test_layer_styles [out_dir]     # out_dir: keep the project (CI runs rive --verify on it)
"""
import os
import re
import sys
import tempfile
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from rml2ae.ae2rml.convert import Converter as AeToRive     # noqa: E402
from rml2ae.convert import Converter as RiveToAe            # noqa: E402
from rml2ae.model import Project                            # noqa: E402

DEMO = os.path.join(ROOT, "examples", "demo.aep")


class Property:
    """stand-in for a py-aep effect parameter (the converter tells parameters by this class name)"""

    def __init__(self, match_name, name, value):
        self.match_name, self.name, self.value = match_name, name, value
        self.keyframes = []
        self.expression, self.expression_enabled = "", False


class Effect:
    def __init__(self, match_name, name, values):
        self.match_name, self.name, self.enabled, self.property_index = match_name, name, True, 9
        self.properties = [Property(f"{match_name}-{i + 1:04d}", f"p{i + 1}", v) for i, v in enumerate(values)]


class Curves:
    """py-aep Curves stand-in: the master curve inverts, the red one is a gamma 2"""

    class Channel:
        def __init__(self, identity):
            self.is_identity = identity

    channels = {"rgb": Channel(False), "red": Channel(False), "green": Channel(True), "blue": Channel(True),
                "alpha": Channel(True)}

    @staticmethod
    def evaluate(ch, x):
        return {"rgb": 1 - x, "red": x * x}.get(ch, x)


def enable_styles(L, prefixes, **values):
    """switch styles on as AE writes them (enable byte 1 on the container, Blending Options and each style)"""
    ls = L.property("ADBE Layer Styles")
    for k, v in values.items():
        p, n = k.split("__")
        ls.property(p + "/enabled").property(p + "/" + n).value = v
    for g in [ls, ls.property("ADBE Blend Options Group")] + [ls.property(p + "/enabled") for p in prefixes]:
        g._ensure_materialized()
        g._tdsb._enable_flags = 1
        g.__dict__.pop("enabled", None)


@contextmanager
def extra_effects(layer, fxs):
    """layer.effects lists `fxs` after its own effects (py-aep's effects is a read-only property)"""
    cls = None
    for k in type(layer).__mro__:
        if "effects" in k.__dict__:
            cls = k
            break
    orig = cls.__dict__["effects"]

    class Group:
        def __init__(self, real):
            self.properties = list(real.properties) + fxs if real is not None else fxs

    setattr(cls, "effects", property(lambda L: Group(orig.fget(L)) if L is layer else orig.fget(L)))
    try:
        yield
    finally:
        setattr(cls, "effects", orig)


def convert(out):
    conv = AeToRive(DEMO, out, log=lambda *a: None)
    comp = next(c for c in conv.project.compositions if c.name == "Demo")
    card = next(L for L in comp.layers if L.name == "Card")
    title = next(L for L in comp.layers if L.name == "Title")
    m = card.masks.add_property("ADBE Mask Atom")
    sp = m.property("ADBE Mask Shape")
    sv = type(sp.value)()
    sv.vertices, sv.in_tangents, sv.out_tangents, sv.closed = [[-200, 0], [0, -80], [200, 0]], [[0, 0]] * 3, [[0, 0]] * 3, False
    sp.value = sv
    enable_styles(card, ["dropShadow", "outerGlow", "innerShadow", "innerGlow", "solidFill", "gradientFill", "frameFX",
                         "bevelEmboss"],
                  dropShadow__distance=12.0, dropShadow__chokeMatte=20.0, dropShadow__localLightingAngle=90.0,
                  solidFill__opacity=30.0, frameFX__size=6.0, frameFX__style=3.0, innerShadow__distance=4.0)
    enable_styles(title, ["frameFX", "dropShadow"])
    stroke = Effect("ADBE Stroke", "Stroke", [1, 0, 0, [0, 0.5, 1, 1], 8.0, 100.0, 1.0, 10.0, 60.0, 15.0, 1])
    curves = Effect("ADBE CurvesCustom", "Curves", [Curves()])
    with extra_effects(card, [stroke, curves]):
        conv.convert()
    return Project(out), conv


def by_name(p, name):
    return next(e for e in p.by_id.values() if e.name == name)


def check(out):
    p, conv = convert(out)
    card = by_name(p, "Card")
    order = [c.name for c in card.children]
    # Photoshop's stacking: Stroke effect on top, then the styles above the layer, the layer, the styles below
    assert order == ["Card · Stroke", "Card · stroke", "Card · inner shadow", "Card · inner glow", "Card · color overlay",
                     "Card · gradient overlay", "Groupe 1", "Card · drop shadow", "Card · glow", "Card · shadow"], order
    paint = lambda node, tag: next(e for e in node.iter() if e.tag == tag)
    # Drop Shadow style: light from 90° (above) -> straight down 12 px, 75 % black, multiply, Spread 20 % of size 5
    sh = by_name(p, "Card · shadow")
    assert abs(sh.num("x")) < 1e-6 and abs(sh.num("y") - 12) < 1e-6, sh.attrs
    assert paint(sh, "Shape").get("blendModeValue") == "multiply"
    assert paint(sh, "SolidColor").get("colorValue") == "BF000000"
    spread = next(e for e in sh.iter() if e.tag == "Stroke" and e.name == "Spread")
    assert abs(spread.num("thickness") - 2.0) < 1e-6, spread.attrs
    # Inner Shadow / Inner Glow: Rive's inner feather, the shadow offset along the light
    fe = paint(by_name(p, "Card · inner shadow"), "Feather")
    assert fe.get("inner") == "true" and fe.num("offsetY") > 0, fe.attrs
    assert paint(by_name(p, "Card · inner glow"), "Feather").get("inner") == "true"
    # Stroke style, Center -> Rive Stroke position "center"
    st = paint(by_name(p, "Card · stroke"), "Stroke")
    assert st.get("position") == "center" and abs(st.num("thickness") - 6) < 1e-6, st.attrs
    assert not any(e.tag == "Fill" for e in by_name(p, "Card · stroke").iter())
    # Color Overlay at 30 %: the colour on a copy, the layer's own paint untouched
    assert paint(by_name(p, "Card · color overlay"), "SolidColor").get("colorValue") == "4DFF0000"
    # Gradient Overlay at 90°: bottom -> top across the card's box (520 x 300, centred)
    gr = paint(by_name(p, "Card · gradient overlay"), "LinearGradient")
    assert abs(gr.num("startY") - 150) < 1e-3 and abs(gr.num("endY") + 150) < 1e-3 and abs(gr.num("startX")) < 1e-3, gr.attrs
    # Curves on the card's colour: master inverts, red squared -> F5F5EB becomes (1 - 0.96)^2, 0.04, 0.08
    col = paint(next(c for c in card.children if c.name == "Groupe 1"), "SolidColor").get("colorValue")
    r, g, b = (int(col[i:i + 2], 16) for i in (2, 4, 6))
    assert (r, g, b) == (0, 10, 20), col
    # Stroke effect: the open mask stroked, 8 px, trimmed 10 % -> 60 %, on top of the layer
    sfx = by_name(p, "Card · Stroke")
    assert any(e.tag == "PointsPath" for e in sfx.iter())
    s = paint(sfx, "Stroke")
    assert abs(s.num("thickness") - 8) < 1e-6 and paint(sfx, "SolidColor").get("colorValue") == "FF0080FF", s.attrs
    tr = paint(sfx, "TrimPath")
    assert abs(tr.num("start") - 0.1) < 1e-6 and abs(tr.num("end") - 0.6) < 1e-6, tr.attrs
    # a text layer: the stroke goes in the text style, the shadow is a feathered copy
    title = by_name(p, "Title")
    assert [c.name for c in title.children] == ["Title · stroke", "Title", "Title · shadow"]
    assert paint(by_name(p, "Title · stroke"), "Stroke").get("position") == "outside"
    # Bevel and Emboss: reported, nothing else
    rep = open(os.path.join(out, "build", "ae2rml", "report.md"), encoding="utf-8").read()
    assert "Bevel and Emboss is not converted" in rep
    assert conv.report.counts()["unsupported"] == 1, conv.report.counts()
    # every key still points at an element that exists (the copies' replaced paints dropped their keys)
    ids = set(p.by_id)
    keyed = re.findall(r'<KeyedObject objectId="([^"]+)"', open(os.path.join(out, "scene.rml"), encoding="utf-8").read())
    assert set(keyed) <= ids, set(keyed) - ids
    # rml2ae reads the result (the copies come back as plain shapes)
    text = RiveToAe(Project(out), os.path.join(out, "build", "rml2ae"), replay=False).convert()
    assert "Card \\u00b7 color overlay" in text, "rml2ae lost the style copies"


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="layer_styles_")
    check(out)
    print(f"test_layer_styles: ok (8 styles + text, Curves, Stroke effect, rml2ae) -> {out}")


if __name__ == "__main__":
    main()
