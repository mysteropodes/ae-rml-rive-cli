"""Rive interpolators After Effects has no ease for, both ways (no After Effects, no Rive CLI):

  rml2ae: tests/cases/easings — ElasticInterpolator (in, out, in-out; amplitude below and above 1) and a
          CubicValueInterpolator bump become one linear AE key per frame on the runtime's curve; a CubicValueInterpolator
          between two different values becomes the same bezier (a normalised cubic ease); a CubicEaseInterpolator is
          unchanged
  ae pull: the per-frame keys read back from AE fold into the original interpolators (written as RML and read again);
          a segment edited in AE stays as its AE keys

    python -m rml2ae.tests.test_easings
"""
import math
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from rml2ae.convert import Converter as RiveToAe, value_at     # noqa: E402
from rml2ae.model import Project, elastic_progress, segment_value   # noqa: E402
from rml2ae.pull import RmlText, fold_baked, keys_to_rive       # noqa: E402

CASE = os.path.join(HERE, "cases", "easings")
X, Y, ROT = ("0:10", 13), ("0:10", 14), ("0:20", 15)


def raw_value(raw, f):
    """the runtime's value of a raw key list at frame f (interpolator of the segment's first key; on a key, its value)"""
    if f <= raw[0][0]:
        return raw[0][1]
    for (f0, v0, i0, e0), (f1, v1, _i, _e) in zip(raw, raw[1:]):
        if f0 == f:
            return v0
        if f0 < f < f1:
            return segment_value(v0, v1, (f - f0) / (f1 - f0), i0, e0)
    return raw[-1][1]


def check_curves():
    # rive-runtime elastic_ease.cpp at its ends and in its middle
    for e in (0, 1, 2):
        for a, p in ((1, 0.3), (0.6, 0.4), (1.5, 0.5)):
            assert abs(elastic_progress(0, e, a, p)) < 2e-3, (e, a, p)
            assert abs(elastic_progress(1, e, a, p) - 1) < 2e-3, (e, a, p)
    assert max(elastic_progress(i / 100, 1, 1, 0.3) for i in range(101)) > 1.2        # out overshoots its end
    assert min(elastic_progress(i / 100, 0, 1, 0.3) for i in range(101)) < -0.2       # in undershoots its start
    assert abs(elastic_progress(0.5, 2, 1, 0.3) - 0.5) < 1e-6                          # in-out: centred
    assert abs(elastic_progress(0.25, 1, 1, 0) - elastic_progress(0.25, 1, 1, 0.5)) < 1e-12   # period 0 -> 0.5
    # a value bump: the cubic's control values, not a progress
    assert abs(segment_value(100, 100, 0.5, "cubicValue", (0.3, 20, 0.7, 20)) - 40) < 1e-6


def check_build(tmp):
    p = Project(CASE)
    anim = next(iter(p.animations.values()))
    raw, baked = anim.raw_keys, anim.keys
    assert [k[2] for k in raw[X]] == ["elastic"] * 3 + ["hold"]
    assert raw[X][1][3] == (2, 0.6, 0.4) and raw[X][2][3] == (0, 1.5, 0.5)
    assert [k[2] for k in raw[Y]] == ["cubicValue", "cubicValue", "hold"]
    # x: one linear key per frame, each on the runtime curve
    assert [k[0] for k in baked[X]] == list(range(61)), [k[0] for k in baked[X]]
    assert all(k[2] == "linear" for k in baked[X][:-1])
    # y: the bump baked per frame, the cubic value ease a normalised cubic ease on its two keys
    assert [k[0] for k in baked[Y]] == list(range(31)) + [60]
    assert baked[Y][30][2] == "cubic"
    e = baked[Y][30][3]
    assert abs(e[1] - 2.0) < 1e-9 and abs(e[3] - 0.5) < 1e-9, e
    assert baked[ROT] == raw[ROT]                                       # a cubic ease is not touched
    # the AE key lists give the runtime value at every frame (and half frame for the exact bezier)
    for k in (X, Y, ROT):
        for f in range(61):
            assert abs(value_at(baked[k], f) - raw_value(raw[k], f)) < 1e-6, (k, f)
    for h in range(30, 60):
        assert abs(value_at(baked[Y], h + 0.5) - raw_value(raw[Y], h + 0.5)) < 1e-4, h
    assert any(v > 500 for _f, v, _i, _e in baked[X][:20])              # the elastic overshoot is there
    # the build: x keyed frame by frame, nothing approximated
    out = os.path.join(tmp, "build")
    conv = RiveToAe(Project(CASE), out, replay=False)
    text = conv.convert()
    assert "elastic ease on x" in str(conv.rep.entries), conv.rep.entries
    bad = [x for x in conv.rep.entries if x[1] in ("unsupported", "approx") and "nterpol" in x[3]]
    assert not bad, bad
    assert text.count('"l",0,0,0,0]') >= 60


def ae_dump(keys, fps):
    """a pull dump record of linear AE keys (what the build wrote for a baked property)"""
    return [{"t": f / fps, "v": v, "i": "l", "o": "l", "ie": [[0, 16.67]], "oe": [[0, 16.67]]} for f, v, _i, _e in keys]


def check_pull(tmp):
    proj = os.path.join(tmp, "proj")
    shutil.copytree(CASE, proj)
    p = Project(proj)
    anim = next(iter(p.animations.values()))
    raw, baked = anim.raw_keys[X], anim.keys[X]
    new = keys_to_rive(ae_dump(baked, anim.fps), None, anim.fps, anim.speed, 1.0)
    assert len(new) == 61
    folded = fold_baked(new, raw, 0.01)
    assert [(f, i) for f, _v, i, _e in folded[:-1]] == [(f, i) for f, _v, i, _e in raw[:-1]], folded   # last: leads nowhere
    assert all(a[3] == b[3] for a, b in zip(folded[:-1], raw[:-1]))
    # the folded keys written as RML read back as the original interpolators
    edited = list(folded)
    edited[-1] = (60, 650.0, "hold", None)                              # an edit outside the elastic segments
    f = RmlText(os.path.join(proj, "scene.rml"))
    assert f.set_keys(anim.id, X[0], X[1], edited)
    f.save()
    again = next(iter(Project(proj).animations.values())).raw_keys[X]
    assert [(k[0], k[2], k[3]) for k in again] == [(k[0], k[2], k[3]) for k in raw], again
    assert again[-1][1] == 650.0
    # a value edited inside the second segment: that segment keeps its AE keys, the others fold
    touched = list(new)
    touched[25] = (touched[25][0], touched[25][1] + 30, "linear", None)
    part = fold_baked(touched, raw, 0.01)
    assert [k[2] for k in part if k[0] in (0, 40)] == ["elastic", "elastic"]
    assert sum(1 for k in part if 20 <= k[0] < 40) == 20 and all(k[2] == "linear" for k in part if 20 <= k[0] < 40)
    # the bump folds back too; the cubic value ease comes back from AE as a cubic ease (same curve, nothing to fold)
    rawy, bakedy = anim.raw_keys[Y], anim.keys[Y]
    newy = keys_to_rive(ae_dump(bakedy[:31], anim.fps), None, anim.fps, anim.speed, 1.0) + [bakedy[30], bakedy[31]]
    newy = newy[:30] + newy[31:]
    foldy = fold_baked(newy, rawy, 0.01)
    assert [k[2] for k in foldy[:-1]] == ["cubicValue", "cubic"], foldy
    assert foldy[0][3] == rawy[0][3]
    assert math.isclose(foldy[1][3][1], 2.0)


def main():
    tmp = tempfile.mkdtemp(prefix="easings_")
    check_curves()
    check_build(tmp)
    check_pull(tmp)
    print("test_easings: ok (elastic in / out / in-out, cubic value bump and ease, build, pull fold-back)")


if __name__ == "__main__":
    main()
