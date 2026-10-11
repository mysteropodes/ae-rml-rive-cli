"""`ae pull` of what was added in After Effects (layers, vector groups), checked on a hand-written AE dump (no After
Effects needed):

    python -m rml2ae.tests.test_pull_added [--out DIR]

--out writes the pulled project there (CI loads it with `rive DIR --verify`).
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from rml2ae import pull                      # noqa: E402
from rml2ae.model import Project             # noqa: E402
from rml2ae.tests.test_pull_contents import fill, path, prop, stroke   # noqa: E402

SCENE = '''<Rive version="1" kind="fragment">
<Artboard width="800" height="600" name="Main" id="0:1">
<Fill name="Bg"><SolidColor colorValue="FF202020" name="C"/></Fill>
<Shape x="100" y="100" name="Box" id="0:10"><Rectangle width="100" height="50" name="R" id="0:11"/>
<Fill name="F" id="0:12"><SolidColor colorValue="FF2266FF" name="C" id="0:13"/></Fill></Shape>
<Node x="400" y="300" name="Rig" id="0:20">
<Shape name="Dot" id="0:21"><Ellipse width="20" height="20" name="E" id="0:23"/><Fill name="F" id="0:24"><SolidColor colorValue="FFFF0000" name="C" id="0:25"/></Fill></Shape>
<Shape x="30" name="Bar" id="0:22"><Rectangle width="40" height="10" name="R" id="0:26"/><Fill name="F" id="0:27"><SolidColor colorValue="FF00FF00" name="C" id="0:28"/></Fill></Shape>
</Node>
<LinearAnimation fps="25" duration="50" name="Idle" id="0:2"/>
</Artboard>
</Rive>
'''
GROUPS = {"rive:0:20": [[[1], "0:21"], [[2], "0:22"]]}


def tf(x=0, y=0, a=(0, 0)):
    return {"sep": False, "p": prop([x, y, 0]), "a": prop(list(a) + [0]), "r": prop(0), "s": prop([100, 100, 100]), "o": prop(100)}


def group(pth, name, items, x=0, y=0):
    return {"path": pth, "n": name, "enabled": True, "p": prop([x, y]), "a": prop([0, 0]), "r": prop(0), "s": prop([100, 100]),
            "o": prop(100), "items": items}


def ellipse(w, h, x=0, y=0, n="Ellipse"):
    return {"m": "ellipse", "size": prop([w, h]), "pos": prop([x, y]), "n": n, "on": True}


def rect(w, h, x=0, y=0, n="Rect"):
    return {"m": "rect", "size": prop([w, h]), "pos": prop([x, y]), "rd": prop(0), "n": n, "on": True}


def built():
    """the layers the build made for SCENE: Box on its own layer, Rig one shape layer of two vector groups"""
    box = dict(tf(100, 100), id="0:10", name="Box", enabled=True, tag="rive:0:10", run=False,
               groups=[group([1], "Box", [rect(100, 50, n="R"), dict(fill([0x22 / 255, 0x66 / 255, 1]), n="F")])])
    rig = dict(tf(400, 300), id="0:20", name="Rig", enabled=True, tag="rive:0:20", run=False,
               groups=[group([1], "Dot", [ellipse(20, 20, n="E"), dict(fill([1, 0, 0]), n="F")]),
                       group([2], "Bar", [rect(40, 10, n="R"), dict(fill([0, 1, 0]), n="F")], x=30)])
    return box, rig


def dump(added=(), layers=None, stack=None):
    box, rig = built()
    layers = layers if layers is not None else [box, rig]
    if stack is None:
        tagged = [{"c": "rive:0:10", "lid": 1}, {"c": "rive:0:20", "lid": 2}]
        # the added layers take their index; the tagged ones fill the rest in order
        n = len(tagged) + len(added)
        free = [i for i in range(1, n + 1) if i not in {L["i"] for L in added}]
        stack = [dict(t, i=i, a=[0, 0]) for t, i in zip(tagged, free)] + [{"i": L["i"], "lid": L["lid"], "c": "", "a": [0, 0]} for L in added]
    return [{"tag": "rive:0:1|0:2", "id": 50, "name": "Main", "fps": 25, "dur": 2, "layers": layers, "stack": stack, "added": list(added)}]


def added(i, lid, kind, name, par=0, **kw):
    L = {"lid": lid, "i": i, "par": par, "name": name, "kind": kind, "copy": False, "enabled": True, "in": 0, "out": 2,
         "isMatte": False, "hasMatte": False, "threeD": False, "fx": 0, "masks": 0, "blend": False}
    L.update(tf())
    L.update(kw)
    return L


def run(d, scene=SCENE, out=None, manifest=None):
    tmp = tempfile.mkdtemp()
    proj = out or tmp
    os.makedirs(proj, exist_ok=True)
    open(os.path.join(proj, "scene.rml"), "w", encoding="utf-8").write(scene)
    if out:
        open(os.path.join(proj, "rive.yaml"), "w").write(f"name: {os.path.basename(proj)}\nmain: Main\nlogs:\n"
                                                         "  file: build/rive.log\n  problems: build/problems.log\n")
    b = os.path.join(proj, "build")
    os.makedirs(b, exist_ok=True)
    json.dump({"layout": "industry", "groups": GROUPS}, open(os.path.join(b, os.path.basename(proj) + ".groups.json"), "w"))
    json.dump(d, open(os.path.join(b, "_pull.json"), "w"))
    if manifest is not None:
        json.dump(manifest, open(os.path.join(b, os.path.basename(proj) + ".manifest.json"), "w"))
    changes = pull.apply(proj, b)
    if manifest is not None:
        manifest.clear()
        manifest.update(json.load(open(os.path.join(b, os.path.basename(proj) + ".manifest.json"))))
        manifest["__groups"] = json.load(open(os.path.join(b, os.path.basename(proj) + ".groups.json")))["groups"]
    text = open(os.path.join(proj, "scene.rml"), encoding="utf-8").read()
    tags = os.path.join(b, pull.TAGS_JSX)
    tag_rows = []
    if os.path.exists(tags):
        tag_rows = json.loads(re.search(r"var T = (\[.*?\]), n = 0;", open(tags).read()).group(1))
    Project(proj)                                # the result still parses
    if not out:
        shutil.rmtree(tmp, ignore_errors=True)
    return [c for c in changes if not c.startswith("WARNING rive not found")], text, tag_rows


def order(text, *names):
    pos = [text.index(f'name="{n}"') for n in names]
    return pos == sorted(pos)


def triangle():
    return {"m": "path", "p": prop(path([[0, -20], [20, 20], [-20, 20]])), "n": "Tri", "on": True}


def new_layers():
    """a shape layer (one plain group: a Shape) on top, keyed; a null at the bottom with a solid parented to it"""
    star = added(1, 101, "shape", "Star", groups=[group([1], "Star", [triangle(), dict(fill([1, 1, 0]), n="Yellow", on=True)])], items=[])
    star["p"] = prop([200, 200, 0], [(0, [200, 200, 0]), (1, [300, 200, 0])])
    ctrl = added(4, 102, "null", "Ctrl")
    ctrl["p"] = prop([50, 60, 0])
    plate = added(5, 103, "solid", "Plate", par=4, solid=[100, 40, [0, 0, 1]])
    plate.update(tf(10, 10, (50, 20)))
    changes, text, tags = run(dump([star, ctrl, plate]))
    sid = re.search(r'<Shape x="200" y="200" name="Star" id="(0:\d+)">', text)
    assert sid, text
    sid = sid.group(1)
    assert re.search(r'<PointsPath isClosed="true" name="Tri" id="0:\d+"><StraightVertex x="0" y="-20"/>', text), text
    assert 'colorValue="FFFFFF00"' in text
    assert order(text, "Star", "Box", "Rig", "Ctrl"), text                  # the AE stack is the Rive child order
    assert order(text, "Ctrl", "Plate", "Idle"), text                        # inside the null, before the animation
    m = re.search(r'<Node x="50" y="60" name="Ctrl" id="(0:\d+)"><Shape x="10" y="10" name="Plate" id="0:\d+">'
                  r'<Rectangle name="Plate" id="0:\d+" width="100" height="40" x="0" y="0"/>'
                  r'<Fill name="Fill" id="0:\d+"><SolidColor colorValue="FF0000FF" id="0:\d+"/></Fill></Shape></Node>', text)
    assert m, text
    assert (f'<KeyedObject objectId="{sid}"><KeyedProperty propertyKey="13"><KeyFrameDouble value="200" frame="0" '
            'interpolationType="linear"/><KeyFrameDouble value="300" frame="25"') in text, text
    assert any("layer 'Star' added in AE -> Shape 'Star'" in c and "1 keyed property" in c for c in changes), changes
    assert any("layer 'Ctrl' added in AE -> Node 'Ctrl'" in c for c in changes), changes
    assert sorted(t[1] for t in tags) == [101, 102, 103] and all(t[0] == 50 and t[2].startswith("rive:0:") for t in tags), tags
    # pulled again once AE carries the tags: the three layers are read as built ones, nothing changes
    rows = {t[1]: t[2] for t in tags}
    box, rig = built()
    star_t = dict(star, id=rows[101][5:], tag=rows[101], run=False)
    ctrl_t = dict(ctrl, id=rows[102][5:], tag=rows[102], run=False, groups=[])
    plate_t = dict(plate, id=rows[103][5:], tag=rows[103], run=False, groups=[])
    again, text2, tags2 = run(dump([], layers=[star_t, box, rig, ctrl_t, plate_t], stack=[]), text)
    assert again == [] and text2 == text and tags2 == [], again


def multi_group_layer():
    """a shape layer of two groups, its anchor point moved: a Node and a Shape per group, the anchor taken out of the
    children; a keyed colour"""
    ring = group([1], "Ring", [ellipse(60, 60, n="O"), dict(stroke([1, 1, 1], 4), n="Line", on=True,
                                                                cap={"v": 2}, join={"v": 3})])
    core = group([2], "Core", [ellipse(20, 20, n="C"), dict(fill([1, 0, 0]), n="Red", on=True)], x=5, y=5)
    core["items"][1]["c"] = prop([1, 0, 0, 1], [(0, [1, 0, 0, 1]), (1, [0, 0, 1, 1])])
    L = added(3, 201, "shape", "Badge", groups=[ring, core], items=[])
    L.update(tf(600, 100, (10, 0)))
    changes, text, tags = run(dump([L]))
    m = re.search(r'<Node x="600" y="100" name="Badge" id="0:\d+"><Shape x="-10" name="Ring" id="0:\d+">.*?</Shape>'
                  r'<Shape x="-5" y="5" name="Core" id="0:\d+">.*?</Shape></Node>', text, re.S)
    assert m, text
    assert re.search(r'<Stroke thickness="4" cap="round" join="bevel" name="Line" id="0:\d+">', text), text
    assert re.search(r'<KeyFrameColor value="FFFF0000" frame="0" interpolationType="linear"/><KeyFrameColor value="FF0000FF" frame="25"', text), text
    assert order(text, "Rig", "Badge", "Idle")
    assert len(tags) == 1 and tags[0][1] == 201


def not_pulled():
    """text, 3D and track matte layers are listed, not written"""
    t = added(1, 301, "text", "Title")
    d3 = added(2, 302, "null", "Cam rig", threeD=True)
    mt = added(3, 303, "shape", "Matte", isMatte=True, groups=[], items=[])
    fx = added(4, 304, "null", "Fx", fx=2, masks=1)
    changes, text, tags = run(dump([t, d3, mt, fx]))
    assert any("'Title' added in AE: not pulled (a text layer)" in c for c in changes), changes
    assert any("'Cam rig' added in AE: not pulled (a 3D layer)" in c for c in changes), changes
    assert any("'Matte' added in AE: not pulled (the track matte of another layer)" in c for c in changes), changes
    assert any("'Fx' added in AE -> Node" in c and "its effects are not pulled" in c and "its masks are not pulled" in c for c in changes), changes
    assert 'name="Title"' not in text and 'name="Matte"' not in text and [r[1] for r in tags] == [304]


def copied_layer():
    """AE's Duplicate keeps the comment: the copy (higher layer id) becomes a new element"""
    box, _rig = built()
    cp = dict(added(1, 401, "shape", "Box", copy=True), groups=copy.deepcopy(box["groups"]), items=[])
    cp.update(tf(150, 100))
    d = dump([cp], stack=[{"i": 1, "lid": 401, "c": "rive:0:10", "a": [0, 0]}, {"i": 2, "lid": 1, "c": "rive:0:10", "a": [0, 0]},
                          {"i": 3, "lid": 2, "c": "rive:0:20", "a": [0, 0]}])
    changes, text, tags = run(d)
    assert len(re.findall(r'name="Box"', text)) == 2 and re.search(r'<Shape x="150" y="100" name="Box" id="0:\d+">', text), text
    assert text.index('x="150" y="100" name="Box"') < text.index('x="100" y="100" name="Box"')
    assert any("a copy of a built layer" in c for c in changes), changes


def parented_to_shape():
    """a null parented to a static shape layer: Rive hangs nothing from a shape, the null becomes its sibling with the
    shape's transform composed"""
    n = added(1, 501, "null", "Tip", par=2)
    n["p"] = prop([10, 0, 0])
    changes, text, tags = run(dump([n]))
    assert '<Node x="110" y="100" name="Tip" id="0:' in text, text
    assert order(text, "Tip", "Box")


def new_group():
    """a vector group added between two groups of a layer the build grouped: a Shape between them in the node; the
    groups after it are still read by name, not by their shifted rank"""
    _box, rig = built()
    tri = group([2], "Tri", [triangle(), dict(stroke([1, 1, 1], 3), n="S", on=True)], x=-30)
    bar = rig["groups"][1]
    bar["path"] = [3]
    bar["p"] = prop([35, 0])                 # Bar moved in AE: still read as Bar
    rig["groups"] = [rig["groups"][0], tri, bar]
    changes, text, tags = run(dump([], layers=[built()[0], rig]))
    assert re.search(r'name="Dot".*?</Shape>\n?<Shape x="-30" name="Tri" id="0:\d+"><PointsPath.*?<Stroke thickness="3" name="S" id="0:\d+">'
                     r'.*?</Shape>\n?<Shape x="35" name="Bar"', text, re.S), text
    assert any("group 'Tri' added in AE to 'Rig' -> Shape" in c for c in changes), changes
    assert any("Bar (0:22) x 30 -> 35" in c for c in changes), changes
    assert tags == []


def original_project():
    """a comp of an original AE project tagged by ae2rml (not in an rml2ae folder): its untagged layers are the ones
    ae2rml did not convert, its groups are not the build's: nothing is added"""
    _box, rig = built()
    rig["groups"].append(group([3], "Tri", [triangle()]))
    d = dump([added(1, 601, "null", "Guide")], layers=[built()[0], rig])
    d[0]["built"] = False
    d[0]["added"] = []                       # the dump script does not read them there
    changes, text, tags = run(d)
    assert changes == [] and text == SCENE and tags == [], changes


def next_build():
    """the manifest after a pull: each added layer's tag is down as stale, so the next build replaces it in place (its
    own block) or removes it (its element now drawn inside another layer); the group map knows the new groups"""
    star = added(1, 101, "shape", "Star", groups=[group([1], "Star", [triangle(), dict(fill([1, 1, 0]), n="Y", on=True)])], items=[])
    ctrl = added(4, 102, "null", "Ctrl")
    plate = added(5, 103, "solid", "Plate", par=4, solid=[100, 40, [0, 0, 1]])
    _box, rig = built()
    rig["groups"].append(group([3], "Tri", [triangle(), dict(stroke([1, 1, 1], 3), n="S", on=True)]))
    man = {}
    changes, text, tags = run(dump([star, ctrl, plate], layers=[built()[0], rig]), manifest=man)
    rows = {t[1]: t[2] for t in tags}
    for lid in (101, 102, 103):
        assert man.get(f"rive:0:1|0:2#{rows[lid]}") == "pulled", (lid, man)
    tri = re.search(r'<Shape name="Tri" id="(0:\d+)">', text).group(1)
    assert any(tri in [i for _p, i in lst] for lst in man["__groups"].values()), man["__groups"]


def main():
    out = None
    if "--out" in sys.argv:
        out = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
    for t in (new_layers, multi_group_layer, not_pulled, copied_layer, parented_to_shape, new_group, original_project,
              next_build):
        t()
        print("ok", t.__name__)
    if out:
        # one project with every kind of addition, for `rive --verify`
        star = added(1, 101, "shape", "Star", groups=[group([1], "Star", [triangle(), dict(fill([1, 1, 0]), n="Y", on=True)])], items=[])
        ring = added(4, 102, "shape", "Badge", groups=[group([1], "Ring", [ellipse(60, 60), dict(stroke([1, 1, 1], 4), n="L", on=True)]),
                                                        group([2], "Core", [rect(20, 20), dict(fill([1, 0, 0]), n="R", on=True)])], items=[])
        ring["p"] = prop([600, 100, 0], [(0, [600, 100, 0]), (1, [650, 120, 0])])
        ctrl = added(5, 103, "null", "Ctrl")
        plate = added(6, 104, "solid", "Plate", par=5, solid=[100, 40, [0, 0, 1]])
        _box, rig = built()
        rig["groups"].append(group([3], "Tri", [triangle(), dict(stroke([1, 1, 1], 3), n="S", on=True)]))
        shutil.rmtree(out, ignore_errors=True)
        changes, _text, _tags = run(dump([star, ring, ctrl, plate], layers=[built()[0], rig]), out=out)
        print("\n".join("  " + c for c in changes))
        print("wrote", out)


if __name__ == "__main__":
    main()
