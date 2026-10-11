"""Gradient presets (rml2ae/ffx.py): a template's gradient colours are replaced and every RIFX size stays coherent.
Checked on a synthetic template, then on the real ones in rml2ae/presets when they are there:

    python -m rml2ae.tests.test_ffx
"""
import os
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from rml2ae import ffx                                  # noqa: E402


def chunk(cid, body):
    return cid.encode() + struct.pack(">I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def lst(cid, ltype, *kids):
    return chunk(cid, ltype.encode() + b"".join(kids))


def stops_of(xml_text):
    """[(offset, values)] of the colour and alpha stops of AE gradient XML"""
    root = ET.fromstring(xml_text)
    out = {}
    for pair in root.iter("prop.pair"):
        key = pair.find("key")
        if key is not None and key.text in ("Alpha Stops", "Color Stops"):
            arrs = [[float(f.text) for f in a.findall("float")] for a in pair.iter("array")]
            out[key.text] = sorted(arrs)
    return out


def check(template_bytes, kind="fill"):
    tmp = tempfile.mkdtemp()
    tpl = os.path.join(tmp, "tpl.ffx")
    with open(tpl, "wb") as f:
        f.write(template_bytes)
    ffx.TEMPLATES[kind] = tpl                          # absolute: os.path.join keeps it
    stops = [(0.0, (1, 0, 0, 1)), (0.4, (0, 1, 0, 0.5)), (1.0, (0, 0, 1, 1))]
    out = ffx.write_preset(kind, stops, os.path.join(tmp, "out.ffx"))
    with open(out, "rb") as f:
        buf = f.read()
    root = ffx.read(out)
    assert struct.unpack(">I", buf[4:8])[0] + 8 + (len(buf) & 1) == len(buf), "RIFX size"
    assert ffx._write(root) == buf, "round trip"
    (g,) = ffx.gradient_chunks(root)
    st = stops_of(g.data.rstrip(b"\0").decode("utf-8"))
    assert st["Color Stops"] == [[0.0, 0.5, 1, 0, 0, 1], [0.40000001, 0.5, 0, 1, 0, 1], [1.0, 0.5, 0, 0, 1, 1]], st
    assert st["Alpha Stops"] == [[0.0, 0.5, 1], [0.40000001, 0.5, 0.5], [1.0, 0.5, 1]], st
    # untouched chunks keep their bytes
    before = [c.data for c in ffx.read(tpl).walk() if c.kids is None and c.cid != "Utf8"]
    after = [c.data for c in root.walk() if c.kids is None and c.cid != "Utf8"]
    assert before == after
    return True


def synthetic():
    xml = ffx.gradient_xml([(0, (1, 1, 1, 1)), (1, (0, 0, 0, 1))]).encode() + b"\0"
    return lst("RIFX", "FaFX", chunk("head", b"\0" * 7),
               lst("LIST", "tdgp", chunk("tdmn", b"ADBE Vector Group".ljust(40, b"\0")),
                   lst("LIST", "GCst", chunk("Utf8", xml), chunk("misc", b"\1\2\3"))))


if __name__ == "__main__":
    saved = dict(ffx.TEMPLATES)
    check(synthetic())
    print("synthetic template: ok")
    for kind, name in saved.items():
        path = os.path.join(ffx.PRESETS, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                check(f.read(), kind)
            print(f"{name}: ok")
        else:
            print(f"{name}: missing (skipped)")
