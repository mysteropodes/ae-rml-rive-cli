"""Gradient animation presets (.ffx) for native shape-layer gradients.

ExtendScript cannot write the colours of a shape layer's Gradient Fill / Gradient Stroke (measured in AE 2026: any
access to "ADBE Vector Grad Colors" raises a modal error a try/catch does not catch). An animation preset can carry
them: the presets in rml2ae/presets were saved from AE (a shape group holding one placeholder path and one Gradient
Fill or Gradient Stroke), and each Rive gradient becomes a copy of one of them whose gradient XML is replaced. The
.jsx then applies the copy to the layer (applyPreset) and sets everything else (points, type, opacity, stroke).

An .ffx is a RIFX file (big-endian, 4-byte id + 4-byte size, bodies padded to even sizes; LIST chunks carry a
4-byte type before their children). The colours sit in a Utf8 chunk as the same XML a project uses.
"""
import os
import struct

PRESETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets")
TEMPLATES = {"fill": "gfill.ffx", "stroke": "gstroke.ffx"}
# the template group's name (the .jsx finds the applied group by it) and its placeholder path
TEMPLATE_GROUP = {"fill": "GF", "stroke": "GS"}


def template_path(kind):
    return os.path.join(PRESETS, TEMPLATES[kind])


def available(kind=None):
    kinds = [kind] if kind else list(TEMPLATES)
    return all(os.path.isfile(template_path(k)) for k in kinds)


# --- RIFX tree -------------------------------------------------------------------------------------------------------

class Chunk:
    __slots__ = ("cid", "data", "kids", "ltype")

    def __init__(self, cid, ltype=None, kids=None, data=b""):
        self.cid, self.ltype, self.kids, self.data = cid, ltype, kids, data

    def walk(self):
        yield self
        for k in self.kids or ():
            yield from k.walk()


def _parse(buf, start, end):
    out = []
    i = start
    while i + 8 <= end:
        cid = buf[i:i + 4].decode("latin-1")
        size = struct.unpack(">I", buf[i + 4:i + 8])[0]
        body = i + 8
        if cid in ("LIST", "RIFX"):
            out.append(Chunk(cid, buf[body:body + 4].decode("latin-1"), _parse(buf, body + 4, body + size)))
        else:
            out.append(Chunk(cid, data=bytes(buf[body:body + size])))
        i = body + size + (size & 1)
    return out


def _write(ch):
    if ch.kids is not None:
        body = ch.ltype.encode("latin-1") + b"".join(_write(k) for k in ch.kids)
    else:
        body = ch.data
    out = ch.cid.encode("latin-1") + struct.pack(">I", len(body)) + body
    return out + (b"\0" if len(body) & 1 else b"")


def read(path):
    with open(path, "rb") as f:
        buf = f.read()
    root = _parse(buf, 0, len(buf))
    if not root or root[0].cid != "RIFX":
        raise ValueError(f"{path}: not a RIFX file")
    return root[0]


def gradient_chunks(root):
    """the Utf8 chunks holding gradient colour XML"""
    return [c for c in root.walk() if c.cid == "Utf8" and b"Gradient Color Data" in c.data]


# --- gradient XML (the format AE writes; py-aep reads it back for ae2rml) ---------------------------------------------

def _f(v):
    return f"{struct.unpack('<f', struct.pack('<f', float(v)))[0]:.8g}"


def _stops(kind, rows):
    out = ["<prop.pair>", f"<key>{kind} Stops</key>", "<prop.list>", "<prop.pair>", "<key>Stops List</key>", "<prop.list>"]
    for i in sorted(range(len(rows)), key=str):          # AE keys its stops as strings: Stop-0, Stop-1, Stop-10, Stop-2…
        out += ["<prop.pair>", f"<key>Stop-{i}</key>", "<prop.list>", "<prop.pair>", f"<key>Stops {kind}</key>",
                "<array>", "<array.type><float/></array.type>"]
        out += [f"<float>{_f(v)}</float>" for v in rows[i]]
        out += ["</array>", "</prop.pair>", "</prop.list>", "</prop.pair>"]
    out += ["</prop.list>", "</prop.pair>", "<prop.pair>", "<key>Stops Size</key>",
            f"<int type='unsigned' size='32'>{len(rows)}</int>", "</prop.pair>", "</prop.list>", "</prop.pair>"]
    return out


def gradient_xml(stops, version="1.0"):
    """[(position 0..1, (r, g, b, a) 0..1)] -> AE gradient XML: one colour stop and one alpha stop per Rive stop,
    midpoints at 0.5 (a straight blend, as Rive interpolates)"""
    stops = sorted(stops, key=lambda s: s[0])
    if not stops:
        stops = [(0.0, (0, 0, 0, 1)), (1.0, (1, 1, 1, 1))]
    if len(stops) == 1:
        stops = [(0.0, stops[0][1]), (1.0, stops[0][1])]
    alpha = [[p, 0.5, c[3]] for p, c in stops]
    color = [[p, 0.5, c[0], c[1], c[2], 1.0] for p, c in stops]
    lines = ["<?xml version='1.0'?>", "<prop.map version='4'>", "<prop.list>", "<prop.pair>",
             "<key>Gradient Color Data</key>", "<prop.list>"]
    lines += _stops("Alpha", alpha) + _stops("Color", color)
    lines += ["</prop.list>", "</prop.pair>", "<prop.pair>", "<key>Gradient Colors</key>", f"<string>{version}</string>",
              "</prop.pair>", "</prop.list>", "</prop.map>"]
    return "\n".join(lines) + "\n"


def _version_of(data):
    s = data.decode("utf-8", "replace")
    a = s.find("<key>Gradient Colors</key>")
    if a >= 0:
        b, e = s.find("<string>", a), s.find("</string>", a)
        if 0 <= b < e:
            return s[b + 8:e]
    return "1.0"


def write_preset(kind, stops, out_path):
    """copy of the kind's template with its gradient colours replaced by stops; returns out_path"""
    root = read(template_path(kind))
    chunks = gradient_chunks(root)
    if not chunks:
        raise ValueError(f"{TEMPLATES[kind]}: no gradient colours found in the template")
    for c in chunks:
        # AE terminates the text with a NUL when it writes it; keep whatever the template does
        nul = c.data.endswith(b"\0")
        c.data = gradient_xml(stops, _version_of(c.data)).encode("utf-8") + (b"\0" if nul else b"")
    with open(out_path, "wb") as f:
        f.write(_write(root))
    return out_path
