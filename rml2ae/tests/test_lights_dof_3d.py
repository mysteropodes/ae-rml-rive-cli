"""Classic 3D lights, depth of field and per-character 3D text -> Rive (no After Effects, no Rive CLI):

  lights:   three.Lighting reproduces After Effects 2026 renders of a white / grey / red 3D solid under parallel,
            ambient, point and spot lights (falloffs, cone feather, diffuse, specular, shininess, metal): the 8-bit
            values below were read from aerender output. ae2rml bakes the shading into the layer's colours
  depth of field: a Feather on the vector paints of a 3D layer, its strength following the circle of confusion
  per-character 3D: one TextModifierGroup per character, the projection of its own plane

The projects are made in memory with py-aep (py_aep.new), saved to a temporary folder and converted.

    python -m rml2ae.tests.test_lights_dof_3d
"""
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import py_aep                                                   # noqa: E402
from py_aep import LightType as LT                              # noqa: E402

from rml2ae.ae2rml import three                                 # noqa: E402
from rml2ae.ae2rml.convert import Converter as AeToRive         # noqa: E402
from rml2ae.model import Project                                # noqa: E402

NOSPEC = {"ADBE Specular Coefficient": 0}
DIFF100 = {**NOSPEC, "ADBE Diffuse Coefficient": 100}
SPOT = lambda f: {"ADBE Light Cone Angle": 60, "ADBE Light Cone Feather 2": f}
# name: (solid colour, material, [(type, position, intensity, colour, options)], {(x, y): AE 2026's 8-bit RGB})
# a 300 px solid centred in a 400 px comp at z = 0, the default camera; lights point along +z (no auto-orient)
CASES = {
    "par_default": ((1, 1, 1), {}, [(LT.PARALLEL, (200, 200, -500), 100, None, {})],
                    {(200, 200): 255, (60, 200): 250, (60, 60): 246}),
    "par_diff50": ((1, 1, 1), NOSPEC, [(LT.PARALLEL, (200, 200, -500), 100, None, {})], {(200, 200): 128, (60, 60): 128}),
    "par_i50": ((1, 1, 1), NOSPEC, [(LT.PARALLEL, (200, 200, -500), 50, None, {})], {(200, 200): 64}),
    "amb50": ((1, 1, 1), {}, [(LT.AMBIENT, (200, 200, -500), 50, None, {})], {(200, 200): 128, (60, 60): 128}),
    "amb50_coef50": ((1, 1, 1), {"ADBE Ambient Coefficient": 50}, [(LT.AMBIENT, (200, 200, -500), 50, None, {})],
                     {(200, 200): 64}),
    "spec50_s5": ((1, 1, 1), {"ADBE Diffuse Coefficient": 0}, [(LT.PARALLEL, (200, 200, -500), 100, None, {})],
                  {(200, 200): 127, (130, 200): 126, (60, 200): 123, (60, 60): 118}),
    "spec50_s50": ((1, 1, 1), {"ADBE Diffuse Coefficient": 0, "ADBE Shininess Coefficient": 50},
                   [(LT.PARALLEL, (200, 200, -500), 100, None, {})],
                   {(200, 200): 126, (130, 200): 116, (60, 200): 87, (60, 60): 61}),
    "red_metal0": ((1, .2, .2), {"ADBE Diffuse Coefficient": 0, "ADBE Metal Coefficient": 0},
                   [(LT.PARALLEL, (200, 200, -500), 100, None, {})], {(200, 200): (127, 127, 127), (60, 60): (118, 118, 118)}),
    "red_metal100": ((1, .2, .2), {"ADBE Diffuse Coefficient": 0}, [(LT.PARALLEL, (200, 200, -500), 100, None, {})],
                     {(200, 200): (127, 25, 25), (60, 60): (118, 24, 24)}),
    "point_none": ((1, 1, 1), DIFF100, [(LT.POINT, (200, 200, -300), 100, None, {})],
                   {(200, 200): 255, (130, 200): 248, (60, 200): 231, (60, 60): 213}),
    "spot_f50": ((1, 1, 1), DIFF100, [(LT.SPOT, (200, 200, -300), 100, None, SPOT(50))],
                 {(200, 200): 255, (288, 200): 241, (304, 200): 212, (320, 200): 160, (336, 200): 96, (60, 60): 0}),
    "spot_f0": ((1, 1, 1), DIFF100, [(LT.SPOT, (200, 200, -300), 100, None, SPOT(0))],
                {(60, 200): 231, (60, 60): 0}),
    "point_smooth": ((1, 1, 1), DIFF100, [(LT.POINT, (200, 200, -300), 100, None,
                                          {"ADBE Light Falloff Type": 2, "ADBE Light Falloff Start": 300,
                                           "ADBE Light Falloff Distance": 200})],
                     {(200, 200): 255, (60, 200): 216, (60, 60): 168}),
    "point_invsq": ((1, 1, 1), DIFF100, [(LT.POINT, (200, 200, -300), 100, None,
                                          {"ADBE Light Falloff Type": 3, "ADBE Light Falloff Start": 200})],
                    {(200, 200): 113, (130, 200): 105, (60, 200): 84, (60, 60): 66}),
    "color": ((1, 1, 1), DIFF100, [(LT.PARALLEL, (200, 200, -500), 100, (1, .5, .25), {})], {(200, 200): (255, 128, 64)}),
    "gray": ((.5, .5, .5), DIFF100, [(LT.PARALLEL, (200, 200, -500), 100, None, {})], {(200, 200): 128}),
    "two": ((1, 1, 1), DIFF100, [(LT.PARALLEL, (200, 200, -500), 40, None, {}), (LT.AMBIENT, (0, 0, -500), 30, None, {})],
            {(200, 200): 179}),
    "noaccept": ((1, 1, 1), {"ADBE Accepts Lights": 0}, [(LT.PARALLEL, (200, 200, -500), 100, None, {})], {(60, 60): 255}),
}


def solid(c, color, mat, z=0.0, size=300):
    s = c.add_solid(list(color), "S", size, size)
    s.three_d_layer = True
    s.transform.property("ADBE Position").value = [200, 200, z]
    m = s.property("ADBE Material Options Group")
    for k, v in mat.items():
        m.property(k).value = v
    return s


def light(c, kind, pos, intensity, color, opts):
    L = c.add_light("L", [200, 200])
    L.light_type = kind
    L.transform.property("ADBE Position").value = list(pos)
    o = L.property("ADBE Light Options Group")
    o.property("ADBE Light Intensity").value = intensity
    o.property("ADBE Light Color").value = list(color or (1, 1, 1)) + [1.0]
    for k, v in opts.items():
        o.property(k).value = v


def shape_rect(c, color=(1, 1, 1, 1), z=0.0, three_d=True):
    S = c.add_shape()
    S.three_d_layer = three_d
    S.transform.property("ADBE Position").value = [200, 200, z]
    v = S.property("ADBE Root Vectors Group").add_property("ADBE Vector Group").property("ADBE Vectors Group")
    v.add_property("ADBE Vector Shape - Rect").property("ADBE Vector Rect Size").value = [120, 120]
    v.add_property("ADBE Vector Graphic - Fill").property("ADBE Vector Fill Color").value = list(color)
    return S


def camera(c, dof=False, focus=None):
    C = c.add_camera("Cam", [200, 200])
    o = C.property("ADBE Camera Options Group")
    zoom = o.property("ADBE Camera Zoom").value
    C.transform.property("ADBE Position").value = [200, 200, -zoom]
    o.property("ADBE Camera Depth of Field").value = 1 if dof else 0
    o.property("ADBE Camera Focus Distance").value = focus or zoom
    return C, zoom, o.property("ADBE Camera Aperture").value


def build(tmp):
    app = py_aep.new()
    root = app.project.root_folder
    for name, (col, mat, lights, _) in CASES.items():
        c = root.add_comp(name, 400, 400, 1.0, 1.0, 25.0)
        solid(c, col, mat)
        for spec in lights:
            light(c, *spec)
    # ae2rml: a lit 3D shape layer (diffuse 50 %, no highlight), its light dimming from 100 % to 0 %
    c = root.add_comp("lit_shape", 400, 400, 1.0, 1.0, 25.0)
    S = shape_rect(c)
    S.property("ADBE Material Options Group").property("ADBE Specular Coefficient").value = 0
    light(c, LT.PARALLEL, (200, 200, -500), 100, None, {})
    c = root.add_comp("dimming", 400, 400, 1.0, 1.0, 25.0)
    shape_rect(c)
    light(c, LT.AMBIENT, (0, 0, 0), 100, None, {})
    p = [L for L in c.layers if type(L).__name__ == "LightLayer"][0].property("ADBE Light Options Group") \
        .property("ADBE Light Intensity")
    p.set_value_at_time(0.0, 100.0)
    p.set_value_at_time(0.96, 0.0)
    # depth of field: a shape 500 px behind the focus plane, one in it
    for name, z in (("dof_far", 500.0), ("dof_focus", 0.0)):
        c = root.add_comp(name, 400, 400, 1.0, 1.0, 25.0)
        shape_rect(c, z=z)
        camera(c, dof=True)
    # per-character 3D: a Y turn, a z offset
    for name, mn, v in (("pc_roty", "ADBE Text Rotation Y", 60), ("pc_posz", "ADBE Text Position 3D", [0, 0, -300]),
                        ("pc_flat", "ADBE Text Rotation", 30)):
        c = root.add_comp(name, 400, 400, 1.0, 1.0, 25.0)
        T = c.add_text("HIM")
        T.three_d_per_char = True
        T.three_d_layer = True
        T.transform.property("ADBE Position").value = [80, 250, 0]
        a = T.text.property("ADBE Text Animators").add_property("ADBE Text Animator")
        a.property("ADBE Text Animator Properties").add_property(mn).value = v
    path = os.path.join(tmp, "lights_dof_3d.aep")
    app.project.save(path)
    return path


def check_lighting(conv):
    worst = 0.0
    for comp in conv.project.compositions:
        if comp.name not in CASES:
            continue
        col, _mat, _l, expect = CASES[comp.name]
        pr = conv.engine.projector(comp, None)
        lt = three.Lighting(pr)
        S = next(L for L in comp.layers if type(L).__name__ == "AVLayer")
        for (x, y), want in expect.items():
            want = want if isinstance(want, tuple) else (want,) * 3
            sh = lt.shade(S, 0.0, (x + 0.5 - 50, y + 0.5 - 50))       # comp pixel -> solid pixel (the plane is at
            A, B = sh if sh else ([1.0] * 3, [0.0] * 3)                # the default camera's zoom: 1 px = 1 px)
            got = [255 * min(1.0, max(0.0, col[k] * A[k] + B[k])) for k in range(3)]
            err = max(abs(got[k] - want[k]) for k in range(3))
            worst = max(worst, err)
            assert err <= 2.0, (comp.name, (x, y), [round(g, 1) for g in got], want)
    return worst


def rml_of(out):
    return open(os.path.join(out, "scene.rml"), encoding="utf-8").read()


def check_convert(path, tmp):
    def run(name):
        out = os.path.join(tmp, name)
        conv = AeToRive(path, out, log=lambda *a: None, comp=name)
        conv.convert()
        return Project(out), conv, out
    # a lit shape: its white fill becomes 50 % grey; the light itself is converted, nothing unsupported
    p, conv, out = run("lit_shape")
    cols = [e.get("colorValue") for e in p.by_id.values() if e.tag == "SolidColor" and e.parent.parent.tag != "Artboard"]
    assert "FF808080" in cols, cols
    assert conv.report.counts()["unsupported"] == 0
    # a dimming light keys the colour
    p, conv, out = run("dimming")
    text = rml_of(out)
    assert "KeyFrameColor" in text, "dimming light: no colour keys"
    # depth of field: Feather strength 2σ, σ = DOF_K × aperture × |d − F| / d × zoom / F; nothing in focus
    p, conv, out = run("dof_far")
    fe = [e for e in p.by_id.values() if e.tag == "Feather"]
    assert len(fe) == 1, fe
    comp = next(c for c in conv.project.compositions if c.name == "dof_far")
    C = next(L for L in comp.layers if type(L).__name__ == "CameraLayer")
    o = C.property("ADBE Camera Options Group")
    zoom, ap = o.property("ADBE Camera Zoom").value, o.property("ADBE Camera Aperture").value
    d = zoom + 500.0
    want = 2 * three.DOF_K * ap * 500.0 / d
    assert abs(fe[0].num("strength") - want) < 1e-3 * want, (fe[0].num("strength"), want)
    p, conv, out = run("dof_focus")
    assert not [e for e in p.by_id.values() if e.tag == "Feather"]
    # per-character 3D: one group per character; a z offset scales each character by zoom / (zoom - 300) about its
    # own pivot; a Y turn shrinks it horizontally only
    p, conv, out = run("pc_posz")
    gs = [e for e in p.by_id.values() if e.tag == "TextModifierGroup" and e.name.startswith("3D character")]
    assert len(gs) == 3, [g.name for g in gs]
    k = zoom / (zoom - 300.0)
    for g in gs:
        assert abs(g.num("scaleX") - k) < 1e-3 and abs(g.num("scaleY") - k) < 1e-3, g.attrs
        assert abs(g.num("rotation")) < 1e-6
        assert abs(g.num("y") - (250 - 200) * (k - 1)) < 1e-2, g.attrs       # the comp centre stays put
    p, conv, out = run("pc_roty")
    gs = [e for e in p.by_id.values() if e.tag == "TextModifierGroup" and e.name.startswith("3D character")]
    assert len(gs) == 3
    for g in gs:
        assert 0.3 < g.num("scaleX") < 0.7 and abs(g.num("scaleY") - 1) < 0.02, g.attrs
    assert conv.report.counts()["unsupported"] == 0
    # 2D rotation only (stays in the layer's plane): the ordinary modifier path, no per-character baking
    p, conv, out = run("pc_flat")
    assert not [e for e in p.by_id.values() if e.tag == "TextModifierGroup" and e.name.startswith("3D character")]


def check_spot_cone():
    assert three.spot_cone(10, 60, 0.5) == 1.0 and three.spot_cone(31, 60, 0.5) == 0.0
    # measured profile (AE 2026, cone 60°, feather 50 %): attenuation at the cone's angles
    for ang, want in ((17.83, 0.947), (20.56, 0.787), (23.19, 0.551), (25.72, 0.283)):
        assert abs(three.spot_cone(ang, 60, 0.5) - want) < 0.01, (ang, three.spot_cone(ang, 60, 0.5), want)


def main():
    tmp = tempfile.mkdtemp(prefix="lights_dof_3d_")
    path = build(tmp)
    check_spot_cone()
    conv = AeToRive(path, os.path.join(tmp, "probe"), log=lambda *a: None)
    worst = check_lighting(conv)
    check_convert(path, tmp)
    print(f"test_lights_dof_3d: ok ({len(CASES)} light cases vs AE 2026, worst {worst:.2f} levels; depth of field; "
          "per-character 3D)")


if __name__ == "__main__":
    main()
