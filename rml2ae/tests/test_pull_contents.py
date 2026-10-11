"""`ae pull` of shape contents (fill / stroke colour, stroke thickness, paths, paths and paints added), checked on a hand-written AE dump
(no After Effects needed):

    python -m rml2ae.tests.test_pull_contents
"""
import copy
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from rml2ae import pull                      # noqa: E402
from rml2ae.model import Project             # noqa: E402

SCENE = '''<Rive version="1" kind="fragment">
<Artboard width="800" height="600" name="Main" id="0:1">
<!-- a comment between elements -->
<Shape x="100" y="100" name="Box" id="0:10"><Rectangle width="100" height="50" name="R"/>
<Fill name="F"><SolidColor colorValue="FF2266FF" name="C"/></Fill>
<Stroke thickness="4" name="S"><SolidColor colorValue="FF000000" name="C"/></Stroke></Shape>
<Shape x="300" y="100" name="Line" id="0:20"><PointsPath isClosed="false" name="P" id="0:21">
<StraightVertex x="0" y="0" id="0:22"/><StraightVertex x="200" y="0" id="0:23"/></PointsPath>
<Stroke thickness="2" name="S"><SolidColor colorValue="FF111111" name="C"/></Stroke></Shape>
<Shape x="100" y="300" name="Blob" id="0:30"><PointsPath name="P">
<StraightVertex x="0" y="0"/><StraightVertex x="100" y="0"/><StraightVertex x="50" y="80"/></PointsPath>
<Fill name="F"><SolidColor colorValue="FF00AA00" name="C"/></Fill></Shape>
<Shape x="500" y="300" name="Ghost" id="0:40"><Ellipse width="60" height="60" name="E"/>
<Fill name="Bottom" isVisible="false"><SolidColor colorValue="FF0000FF" name="C"/></Fill>
<Fill name="Top"><SolidColor colorValue="80FFFFFF" name="C"/></Fill></Shape>
<LinearAnimation fps="25" duration="50" name="Idle" id="0:2"/>
</Artboard>
</Rive>
'''


def prop(v, keys=None):
    out = {"v": v}
    if keys:
        out["k"] = [{"t": t, "v": kv, "i": "l", "o": "l", "ie": [[0, 16.67]], "oe": [[0, 16.67]]} for t, kv in keys]
    return out


def shape_layer(oid, x, y, items):
    """a dedicated shape layer as rml2ae builds it: the transform on the layer, the contents in group 1"""
    return {"id": oid, "name": oid, "enabled": True, "sep": False, "p": prop([x, y, 0]), "r": prop(0), "s": prop([100, 100, 100]),
            "o": prop(100), "tag": "rive:" + oid, "run": False,
            "groups": [{"path": [1], "enabled": True, "p": prop([0, 0]), "r": prop(0), "s": prop([100, 100]), "o": prop(100),
                        "items": items}]}


def fill(rgb, op=100):
    return {"m": "fill", "c": prop(list(rgb) + [1]), "o": prop(op)}


def stroke(rgb, w, op=100):
    return {"m": "stroke", "c": prop(list(rgb) + [1]), "o": prop(op), "w": prop(w)}


def path(vs, ins=None, outs=None, closed=True):
    z = [[0, 0]] * len(vs)
    return {"vs": vs, "in": ins or z, "out": outs or z, "c": closed}


def untouched():
    """what the build put in AE for SCENE (AE lists paints in reverse Rive order)"""
    c = lambda h: [int(h[i:i + 2], 16) / 255 for i in (2, 4, 6)]       # noqa: E731
    return [{"tag": "rive:0:1|0:2", "name": "Main", "fps": 25, "layers": [
        shape_layer("0:10", 100, 100, [{"m": "rect", "size": prop([100, 50]), "pos": prop([0, 0])},
                                        stroke(c("FF000000"), 4), fill(c("FF2266FF"))]),
        shape_layer("0:20", 300, 100, [{"m": "path", "p": prop(path([[0, 0], [200, 0]], closed=False))}, stroke(c("FF111111"), 2)]),
        shape_layer("0:30", 100, 300, [{"m": "path", "p": prop(path([[0, 0], [100, 0], [50, 80]]))}, fill(c("FF00AA00"))]),
        shape_layer("0:40", 500, 300, [{"m": "ellipse", "size": prop([60, 60]), "pos": prop([0, 0])},
                                        fill([1, 1, 1], 128 / 255 * 100), fill(c("FF0000FF"), 0)]),
    ]}]


def run(dump, scene=SCENE, dry=False, groups=None):
    d = tempfile.mkdtemp()
    open(os.path.join(d, "scene.rml"), "w", encoding="utf-8").write(scene)
    out = os.path.join(d, "build")
    os.makedirs(out)
    if groups:
        json.dump({"layout": "industry", "groups": groups}, open(os.path.join(out, os.path.basename(d) + ".groups.json"), "w"))
    json.dump(dump, open(os.path.join(out, "_pull.json"), "w"))
    changes = pull.apply(d, out, dry=dry)
    text = open(os.path.join(d, "scene.rml"), encoding="utf-8").read()
    Project(d)                                # the result still parses
    return [c for c in changes if not c.startswith("WARNING rive not found")], text


def layer(dump, oid):
    return next(L for L in dump[0]["layers"] if L["id"] == oid)


def untouched_is_quiet():
    """the build's own values read back: nothing to pull"""
    changes, text = run(untouched())
    assert changes == [], changes
    assert text == SCENE


def element_spans():
    """id-less elements are found by rank under their nearest ancestor with an id, comments skipped"""
    d = tempfile.mkdtemp()
    open(os.path.join(d, "scene.rml"), "w", encoding="utf-8").write(SCENE)
    proj = Project(d)
    f = pull.RmlText(os.path.join(d, "scene.rml"))
    stroke_c = proj.by_id["0:10"].find("Stroke").find("SolidColor")
    sp = f.span(stroke_c)
    assert f.text[sp[0]:sp[2]] == '<SolidColor colorValue="FF000000" name="C"/>'
    v = proj.by_id["0:30"].find("PointsPath").children[2]
    assert f.text[f.span(v)[0]:f.span(v)[2]] == '<StraightVertex x="50" y="80"/>'
    f.set_attr_el(stroke_c, "colorValue", "FF123456")
    assert '<Stroke thickness="4" name="S"><SolidColor colorValue="FF123456" name="C"/></Stroke>' in f.text


def static_edits():
    """colours (alpha from the AE opacity), thickness, rectangle size, a moved vertex with a tangent, a path that gained a
    vertex: written as attributes / rewritten vertices"""
    dump = untouched()
    box = layer(dump, "0:10")["groups"][0]["items"]
    box[0]["size"] = prop([120, 50])
    box[1]["w"] = prop(6)
    box[2] = fill([1, 0, 0], 50)
    line = layer(dump, "0:20")["groups"][0]["items"]
    line[0]["p"] = prop(path([[0, 0], [200, 30]], [[0, 0], [-40, 0]], [[0, 0], [0, 0]], closed=False))
    blob = layer(dump, "0:30")["groups"][0]["items"]
    blob[0]["p"] = prop(path([[0, 0], [100, 0], [100, 80], [0, 80]]))
    ghost = layer(dump, "0:40")["groups"][0]["items"]
    ghost[2] = fill([0, 1, 0], 0)                 # the hidden fill: colour pulled, its alpha is not the AE 0 %
    changes, text = run(dump)
    assert '<Rectangle width="120" height="50" name="R"/>' in text, text
    assert '<Stroke thickness="6" name="S">' in text
    assert '<Fill name="F"><SolidColor colorValue="80FF0000" name="C"/></Fill>' in text
    assert '<StraightVertex x="0" y="0" id="0:22"/><CubicAsymmetricVertex x="200" y="30" rotation="0" inDistance="40" outDistance="0" id="0:23"/>' in text, text
    assert '<StraightVertex x="0" y="0"/><StraightVertex x="100" y="0"/><StraightVertex x="100" y="80"/><StraightVertex x="0" y="80"/>' in text
    assert '<Fill name="Bottom" isVisible="false"><SolidColor colorValue="FF00FF00" name="C"/></Fill>' in text
    assert any("fill colour FF2266FF -> 80FF0000" in c for c in changes), changes
    assert any("path of 3 -> 4 vertices rewritten" in c for c in changes), changes
    # pulled twice: nothing more
    again, text2 = run(dump, text)
    assert again == [] and text2 == text, again


def keyed_edits():
    """keys in AE: stroke colour and thickness keyed (the SolidColor gets an id), path keys on each vertex"""
    dump = untouched()
    box = layer(dump, "0:10")["groups"][0]["items"]
    box[1]["c"] = prop([0, 0, 0, 1], [(0, [0, 0, 0, 1]), (1, [1, 1, 1, 1])])
    box[1]["w"] = prop(4, [(0, 4), (2, 10)])
    line = layer(dump, "0:20")["groups"][0]["items"]
    line[0]["p"] = prop(None, [(0, path([[0, 0], [200, 0]], closed=False)), (1, path([[0, 0], [200, 50]], closed=False))])
    changes, text = run(dump)
    m = re.search(r'<Stroke thickness="4" name="S" id="0:\d+"><SolidColor colorValue="FF000000" name="C" id="(0:\d+)"/>', text)
    assert m, text
    sid = m.group(1)
    assert (f'<KeyedObject objectId="{sid}"><KeyedProperty propertyKey="37"><KeyFrameColor value="FF000000" frame="0" '
            'interpolationType="linear"/><KeyFrameColor value="FFFFFFFF" frame="25" interpolationType="linear"/>') in text, text
    assert '<KeyedProperty propertyKey="47"><KeyFrameDouble value="4" frame="0" interpolationType="linear"/><KeyFrameDouble value="10" frame="50"' in text
    # vertex 2 moved: its y keyed; x and vertex 1 stay constant (no key)
    assert '<KeyedObject objectId="0:23"><KeyedProperty propertyKey="25"><KeyFrameDouble value="0" frame="0" interpolationType="linear"/><KeyFrameDouble value="50" frame="25"' in text, text
    assert 'objectId="0:22"' not in text
    assert any("path keys -> 1 vertex property keyed" in c for c in changes), changes
    again, text2 = run(dump, text)
    assert again == [] and text2 == text, again


def grouped_shape():
    """a shape drawn as a vector group of a shared layer (industry layout) is found through <project>.groups.json"""
    dump = untouched()
    L = layer(dump, "0:30")
    L["id"], L["tag"] = "0:1", "rive:0:1"
    L["groups"][0]["items"][1] = fill([0, 0, 1])
    changes, text = run(dump, groups={"rive:0:1": [[[1], "0:30"]]})
    assert '<Fill name="F"><SolidColor colorValue="FF0000FF" name="C"/></Fill></Shape>\n<Shape x="500"' in text, text
    assert any("Blob (0:30) fill colour FF00AA00 -> FF0000FF" in c for c in changes), changes


def mismatch_reported():
    """a paint added in AE while another one changed colour: which one is new is unclear, that kind is not pulled and a
    line says why; the rest still is"""
    dump = untouched()
    items = layer(dump, "0:10")["groups"][0]["items"]
    items.append(fill([0, 0, 0]))
    items[2] = fill([1, 1, 0])
    items[1]["w"] = prop(9)
    changes, text = run(dump)
    assert any("paints not pulled" in c or "fills not pulled: 2 in AE, 1 in the RML" in c for c in changes), changes
    assert '<Stroke thickness="9" name="S">' in text
    assert 'colorValue="FF2266FF"' in text


def added_contents():
    """a fill and an ellipse added in AE to an existing shape: the existing ones found in order, the new ones written at
    their rank (paints reversed); pulled again: nothing more"""
    dump = untouched()
    items = layer(dump, "0:10")["groups"][0]["items"]
    items.insert(1, {"m": "ellipse", "size": prop([10, 10]), "pos": prop([5, 5]), "n": "Dot"})
    items.append(dict(fill([0, 0, 0]), n="Shadow"))
    items.insert(2, dict(stroke([1, 1, 1], 2), n="Glow"))
    changes, text = run(dump)
    m = re.search(r'<Shape x="100" y="100" name="Box" id="0:10">(.*?)</Shape>', text, re.S)
    body = m.group(1)
    order = re.findall(r'<(Rectangle|Ellipse|Fill|Stroke) [^>]*name="([^"]+)"', body)
    assert order == [("Rectangle", "R"), ("Ellipse", "Dot"), ("Fill", "Shadow"), ("Fill", "F"), ("Stroke", "S"), ("Stroke", "Glow")], order
    assert re.search(r'<Ellipse width="10" height="10" x="5" y="5" name="Dot" id="0:\d+"/>', body), body
    assert any("fill 'Shadow' added in AE" in c for c in changes), changes
    again, text2 = run(dump, text)
    assert again == [] and text2 == text, again


def matte_and_driven_ignored():
    """a matte copy (white fill) and a colour driven by an expression are not edits"""
    dump = untouched()
    L = copy.deepcopy(layer(dump, "0:10"))
    L["matte"] = True
    L["groups"][0]["items"][2] = fill([1, 1, 1])
    dump[0]["layers"].append(L)
    layer(dump, "0:20")["groups"][0]["items"][1]["c"] = dict(prop([1, 0, 0, 1]), x=True)
    changes, text = run(dump)
    assert changes == [] and text == SCENE, changes


def main():
    for t in (element_spans, untouched_is_quiet, static_edits, keyed_edits, grouped_shape, mismatch_reported, added_contents,
              matte_and_driven_ignored):
        t()
        print("ok", t.__name__)


if __name__ == "__main__":
    main()
