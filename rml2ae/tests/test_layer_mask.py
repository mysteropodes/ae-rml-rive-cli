"""Track mattes <-> Rive LayerMask, both ways (no After Effects, no Rive CLI):

  ae2rml: examples/demo.aep's alpha matte, retyped in memory to the four AE matte types, made visible, pointed at a
          text layer -> a LayerMask on the matted layer's node, its source a node of the same artboard holding the
          matte layer as drawn (paints kept); --clip-mattes keeps the old ClippingShape
  rml2ae: tests/cases/layer_mask (a luma matte with a hidden gradient source, an inverted alpha matte with a drawn
          Shape source) and the demo's ae2rml output -> setTrackMatte with the matching TrackMatteType

    python -m rml2ae.tests.test_layer_mask
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from rml2ae.ae2rml.convert import CompBuild, Converter as AeToRive     # noqa: E402
from rml2ae.convert import Converter as RiveToAe            # noqa: E402
from rml2ae.model import El, Project, layer_mask_flags      # noqa: E402

DEMO = os.path.join(ROOT, "examples", "demo.aep")
CASE = os.path.join(HERE, "cases", "layer_mask")


def ae2rml(out, matte_type=None, visible=False, text_matte=False, layer_masks=True):
    conv = AeToRive(DEMO, out, log=lambda *a: None, layer_masks=layer_masks)
    comp = next(c for c in conv.project.compositions if c.name == "Demo")
    stripe = next(L for L in comp.layers if L.name == "Stripe")
    matte = next(L for L in comp.layers if L.name == "Stripe matte")
    if matte_type is not None:
        stripe.track_matte_type = matte_type
    if visible:
        matte.enabled = True
    orig = CompBuild.matte_of
    if text_matte:                    # py-aep's track_matte_layer is read-only: the matte lookup answers instead
        title = next(L for L in comp.layers if L.name == "Title")
        CompBuild.matte_of = lambda cb, L: title if L is stripe else orig(cb, L)
    try:
        conv.convert()
    finally:
        CompBuild.matte_of = orig
    return Project(out), conv


def rml2ae(project_dir, out):
    conv = RiveToAe(Project(project_dir), out, replay=False)
    text = conv.convert()
    return text, conv


def masks_of(p):
    return [e for e in p.by_id.values() if e.tag == "LayerMask"]


def artboard_of(e):
    while e is not None and e.tag != "Artboard":
        e = e.parent
    return e


def check_ae2rml(tmp):
    # the four matte types -> maskModeValue 0-3, isVisible set, sourceDraws clear (the matte layer's eye is off)
    for t, mode in ((5013, 0), (5014, 1), (5015, 2), (5016, 3)):
        p, conv = ae2rml(os.path.join(tmp, f"t{t}"), matte_type=t)
        lms = masks_of(p)
        assert len(lms) == 1, lms
        lm = lms[0]
        assert layer_mask_flags(lm) == (mode, True, False), (t, lm.attrs)
        assert lm.parent.name == "Stripe · matte", lm.parent
        src = p.by_id[lm.get("sourceId")]
        assert src.tag == "Node" and src.name == "Stripe matte · matte source", src
        assert artboard_of(src) is artboard_of(lm), "source in another artboard"
        # drawn as it is: the matte's red fill is kept (a clip source loses its paints)
        assert any(e.tag == "SolidColor" and e.get("colorValue") == "FFFF0000" for e in src.iter()), "matte paint lost"
        assert not any(e.tag == "ClippingShape" for e in p.by_id.values())
        assert conv.report.counts()["unsupported"] == 0
    # a visible matte: drawn once, the mask lets Rive draw its source (sourceDraws)
    p, _ = ae2rml(os.path.join(tmp, "visible"), visible=True)
    lm = masks_of(p)[0]
    assert layer_mask_flags(lm) == (0, True, True), lm.attrs
    assert sum(1 for e in p.by_id.values() if e.name.endswith("· matte source")) == 1
    # a text matte: was unsupported as a clip, a LayerMask reads its pixels
    p, conv = ae2rml(os.path.join(tmp, "text"), text_matte=True)
    lm = masks_of(p)[0]
    src = p.by_id[lm.get("sourceId")]
    assert any(e.tag == "Text" for e in src.iter()), "text matte source has no Text"
    assert conv.report.counts()["unsupported"] == 0
    # --clip-mattes: the geometry clip, as before
    p, _ = ae2rml(os.path.join(tmp, "clip"), layer_masks=False)
    assert not masks_of(p)
    assert any(e.tag == "ClippingShape" for e in p.by_id.values())


def check_flags():
    f = lambda **a: layer_mask_flags(El("LayerMask", a))
    assert f() == (0, True, False)                              # the runtime default, maskFlags = 4
    assert f(maskFlags="13") == (1, True, True)
    assert f(maskFlags="2") == (2, False, False)
    assert f(maskModeValue="invertedLuminance", sourceDraws="true") == (3, True, True)
    assert f(maskModeValue="1", isVisible="false") == (1, False, False)
    assert f(maskModeValue="9") == (0, True, False)             # unknown -> alpha, as the runtime reads it


def check_rml2ae(tmp):
    text, conv = rml2ae(CASE, os.path.join(tmp, "case"))
    mattes = re.findall(r"(clp\d+)\.setTrackMatte\(clp\d+, TrackMatteType\.(\w+)\)", text)
    names = dict(re.findall(r'var (clp\d+) = c\d+\.layers\.add\(sub\d+\); \1\.name = "([^"]+)"', text))
    got = sorted((names[a], t) for a, t in mattes)
    assert got == [("Card", "ALPHA_INVERTED"), ("Photo", "LUMA")], got
    # the matte copies: tagged as the masked node's, read by nobody (ae pull skips "+matte")
    assert 'comment = "rive:0:10+matte|' in text and 'comment = "rive:0:30+matte|' in text
    # Ramp (sourceDraws false) -> its own precomp, switched off; Hole (sourceDraws true) stays on
    ramp = re.search(r'var (clp\d+) = c\d+\.layers\.add\(sub\d+\); \1\.name = "Ramp";', text)
    assert ramp and f"{ramp.group(1)}.enabled = false;" in text, "hidden source not switched off"
    hole = [v for v, n in names.items() if n == "Hole"]
    assert hole and not any(f"{v}.enabled = false;" in text for v in hole)
    # the Shape source copied whole into its matte precomp: the root null is the identity (it would sit at the comp
    # centre and push the hole out of frame, as seen in After Effects)
    hroot = re.search(r'var (root\d+) = sub\d+\.layers\.addNull\(\); \1\.name = "Hole";', text)
    assert hroot and f'tr({hroot.group(1)}, "ADBE Position").setValue([0, 0]);' in text, "Hole matte root not at origin"
    bad = [e for e in conv.rep.entries if e[1] == "unsupported"]
    assert not bad, bad
    # the round trip: ae2rml's LayerMask on the demo -> an AE alpha matte again
    ae2rml(os.path.join(tmp, "rt"), matte_type=5015)
    text, conv = rml2ae(os.path.join(tmp, "rt"), os.path.join(tmp, "rt", "build", "rml2ae"))
    assert re.search(r"setTrackMatte\(clp\d+, TrackMatteType\.LUMA\)", text), "round trip lost the luma matte"


def main():
    tmp = tempfile.mkdtemp(prefix="layer_mask_")
    check_flags()
    check_ae2rml(tmp)
    check_rml2ae(tmp)
    print("test_layer_mask: ok (four matte types, visible matte, text matte, --clip-mattes, rml2ae, round trip)")


if __name__ == "__main__":
    main()
