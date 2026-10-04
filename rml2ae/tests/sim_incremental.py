"""Dry-run check of the incremental rebuild (no After Effects): full build -> manifest ; RML edits -> what render()
decides. Run: python3 -m rml2ae.tests.sim_incremental  (uses tests/cases/features, copied to a temp dir)."""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from rml2ae.model import Project          # noqa: E402
from rml2ae.convert import Converter      # noqa: E402


LAYOUT = "raw"


def build(sim, inc):
    out = os.path.join(sim, "build", "rml2ae")
    os.makedirs(out, exist_ok=True)
    conv = Converter(Project(sim), out, keep_project=inc, replay=False, replace=inc, layout=LAYOUT)
    conv.incremental_mode = inc
    text = conv.convert()
    return conv, text


def rebuilt_tags(text):
    return re.findall(r'log\("rebuilt: ([^"]+)"\)', text)


def main():
    sim = tempfile.mkdtemp(prefix="rml2ae_sim_")
    # build/ is skipped: the Rive viewer may be writing there (features.riv.tmp) while we copy
    shutil.copytree(os.path.join(HERE, "cases", "features"), sim, dirs_exist_ok=True, ignore=shutil.ignore_patterns("build"))
    rml = os.path.join(sim, "scene.rml")
    conv, text = build(sim, False)
    json.dump(conv.manifest, open(conv.manifest_path, "w"))
    assert "__MANIFEST__" not in text
    # 1. nothing changed -> nothing rebuilt
    conv, text = build(sim, True)
    st = conv.stats
    assert st["els_rebuilt"] == 0 and not st["comps_rebuilt"] and st["els_removed"] == 0, st
    assert not rebuilt_tags(text)
    # 2. one colour inside a group -> only that shape, in place, the group kept
    src = open(rml).read()
    open(rml, "w").write(src.replace('colorValue="FFFF2266"', 'colorValue="FF00FF00"', 1))
    conv, text = build(sim, True)
    if LAYOUT == "industry":
        # the group is ONE shape layer (its shapes are vector groups): a colour inside it rebuilds that layer
        assert set(rebuilt_tags(text)) == {"rive:0:116"}, rebuilt_tags(text)
    else:
        assert set(rebuilt_tags(text)) == {"rive:0:118"}, rebuilt_tags(text)        # once per animation comp
        assert re.search(r'var nul\d+ = layerTag\(c\d+, "rive:0:116"\)', text), "the group should be kept (looked up)"
    assert conv.stats["els_removed"] == 0
    json.dump(conv.manifest, open(conv.manifest_path, "w"))
    # 3. a child removed from the Solo -> the Solo rebuilt (its slider rows changed), the child's layers removed
    src = open(rml).read()
    open(rml, "w").write(re.sub(r'<Shape name="S2" id="0:125">.*?</Shape>', "", src, count=1))
    conv, text = build(sim, True)
    assert "rive:0:123" in rebuilt_tags(text) and conv.stats["els_removed"] >= 1, (rebuilt_tags(text), conv.stats)
    assert 'removeTagged(c4, ["rive:0:125"])' in text or '"rive:0:125"' in text
    json.dump(conv.manifest, open(conv.manifest_path, "w"))
    # 4. a new element -> built and placed above its previous sibling (the group), nothing else touched
    src = open(rml).read()
    open(rml, "w").write(src.replace('<Node x="950" y="150" opacity="0.5" name="Group" id="0:116">',
                                     '<Shape x="600" y="400" name="NEW" id="0:999"><Rectangle width="50" height="50" name="R"/>'
                                     '<Fill name="F"><SolidColor colorValue="FF000000" name="C"/></Fill></Shape>\n'
                                     '<Node x="950" y="150" opacity="0.5" name="Group" id="0:116">', 1))
    conv, text = build(sim, True)
    if LAYOUT == "industry":
        # loose sibling shapes share one layer: the new shape joins (or makes) the run it sits in
        assert any(t.startswith("rive:0:999") for t in rebuilt_tags(text)) and "rive:0:116" not in rebuilt_tags(text), rebuilt_tags(text)
    else:
        assert sorted(set(rebuilt_tags(text))) == ["rive:0:999"], rebuilt_tags(text)
    above = r'\["rive:0:116"\]' if LAYOUT == "industry" else r'\["rive:0:116","rive:0:118","rive:0:117"\]'
    assert re.search(r'anchor\(c\d+, \["rive:0:999"\], ' + above, text), "new element not anchored above the group"
    # 5. artboard background changed -> the comps' heads changed -> whole comps rebuilt and relinked
    json.dump(conv.manifest, open(conv.manifest_path, "w"))
    src = open(rml).read()
    open(rml, "w").write(src.replace('colorValue="FFF3F0E8"', 'colorValue="FFFFFFFF"', 1))
    conv, text = build(sim, True)
    assert conv.stats["comps_rebuilt"] and "relinkComp(" in text, conv.stats
    shutil.rmtree(sim)
    print(f"sim_incremental ({LAYOUT}): ok")


if __name__ == "__main__":
    for LAYOUT in ("raw", "industry"):
        main()
