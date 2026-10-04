"""Effects that read a second layer (fxlib mapTex, e.g. Displacement Map): ae2rml binds the Rive node's e<i>_mapSource
input to an artboard showing that layer's source. Checked on stand-in objects (no .aep needed):

    python -m rml2ae.tests.test_map_layer
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from rml2ae.ae2rml import fxlib                          # noqa: E402
from rml2ae.ae2rml.convert import CompBuild              # noqa: E402
from rml2ae.ae2rml.rml import Anim, E                    # noqa: E402
from rml2ae.ae2rml.util import IdPool                    # noqa: E402

NS = types.SimpleNamespace


class Report:
    def __init__(self):
        self.rows = []

    def add(self, scope, kind, name, text):
        self.rows.append((kind, name, text))


def setup(ref_value, target_source=None):
    """a comp of two layers: layer 1 = the effect's layer (Displacement Map, map param = ref_value), layer 2 = target"""
    ids = IdPool()
    rep = Report()
    precomp_ab = E("Artboard", id="pc:1", name="map precomp")
    conv = NS(ids=ids, report=rep, order=[], loop="loop",
              comp_build=lambda src: NS(ab=precomp_ab),
              image_asset=lambda item, L, transform=None, tag="": ("asset:9", 320.0, 180.0))
    mp = NS(match_name="ADBE Displacement Map-0001", value=ref_value)
    fx = NS(match_name="ADBE Displacement Map", name="Displacement Map")
    L1 = NS(name="Layer", id=11)
    L2 = NS(name="Map", id=12, source=target_source)
    lb = NS(L=L1, fx_params=lambda f: [mp], id=lambda *parts: ids.key("lb", *parts))
    cb = NS(conv=conv, layers=[L1, L2], scope="Comp", kid="c1", fx_subs=[],
            anim=Anim("a", "Comp", 25, 10, "loop"))
    sub = NS(ab=E("Artboard", id="sub:1", name="effects"))
    sd = E("ScriptedDrawable", id="sd:1", name="fx")
    CompBuild.bind_map_layer(cb, sd, lb, fx, fxlib.manifest("displacement_map"), 1, sub, ("layer",))
    inputs = [c for c in sd.children if c.tag == "ScriptInputArtboard"]
    return inputs, rep, cb


def main():
    # 1. the layer itself (AE's default for a layer parameter)
    inputs, rep, _ = setup(1)
    assert [(c.name, c.attrs["artboardId"]) for c in inputs] == [("e1_mapSource", "sub:1")], inputs
    # 2. a precomp layer -> its comp artboard
    inputs, rep, _ = setup(2, NS(layers=[]))
    assert [(c.name, c.attrs["artboardId"]) for c in inputs] == [("e1_mapSource", "pc:1")], inputs
    # 3. a still image -> a sub-artboard holding the image
    FileSource = type("FileSource", (), {"is_still": True})      # what py-aep names a file footage source
    img = NS(width=640, height=360, main_source=FileSource())
    inputs, rep, cb = setup(2, img)
    assert len(inputs) == 1 and inputs[0].name == "e1_mapSource", inputs
    fa = cb.fx_subs[0]
    assert inputs[0].attrs["artboardId"] == fa.ab.id
    im = [c for c in fa.ab.children if c.tag == "Image"]
    assert im and im[0].attrs["assetId"] == "asset:9" and abs(im[0].attrs["scaleX"] - 2.0) < 1e-9, im
    # 4. a layer kind that cannot be bound -> no input, reported
    inputs, rep, _ = setup(2, NS())
    assert not inputs and any(k == "approx" and "map layer" in t for k, _n, t in rep.rows), rep.rows
    print("test_map_layer: ok (self, precomp, still image, unsupported)")


if __name__ == "__main__":
    main()
