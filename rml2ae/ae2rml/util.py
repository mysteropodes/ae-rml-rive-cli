"""Small helpers shared by the AE -> RML converter: strings, numbers, colours, ids, report, footage and font lookup."""
import collections
import json
import math
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)                       # rml2ae/
ROOT = os.path.dirname(PKG)                       # the repository root


# ------------------------------------------------------------------ strings / numbers
def clean(s):
    """py-aep hands back localized names decoded with surrogate escapes ('Point d\\udcb4ancrage'): repair them."""
    if s is None:
        return ""
    s = str(s)
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "surrogateescape").decode("cp1252", "replace")


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def esc(s):
    """XML attribute value (newlines kept as character references)."""
    s = _CTRL.sub("", clean(s))
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("\r\n", "&#10;").replace("\r", "&#10;").replace("\n", "&#10;"))


def fmt(v, nd=4):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "0"
    a = abs(float(v))
    if 0 < a < 1:
        # small values keep 5 significant digits (a 3D camera's perspective terms are ~1e-5; a scale of 1/19.2
        # written 0.0521 is already 0.6 px off at 1920 px)
        nd = min(12, max(nd, 4 - int(math.floor(math.log10(a)))))
    s = f"{float(v):.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def argb(rgba, alpha=None):
    """AE colour [r, g, b(, a)] in 0..1 (+ optional alpha override 0..1) -> Rive 'AARRGGBB'."""
    r, g, b = (list(rgba) + [0, 0, 0])[:3]
    a = alpha if alpha is not None else (rgba[3] if len(rgba) > 3 else 1.0)
    # half up, as AE quantises (round() is half-to-even: 0.3 * 255 = 76.5 -> 76 where AE gives 77)
    return "%02X%02X%02X%02X" % tuple(int(clamp(float(c), 0.0, 1.0) * 255 + 0.5) for c in (a, r, g, b))


def slug(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", clean(s)).strip("_") or "item"


# ------------------------------------------------------------------ ids
class IdPool:
    """Rive ids '0:N'. Semantic keys (comp/layer/role…) are remembered in build/ae2rml/idmap.json so a re-import of
    the same .aep gives the same ids (diff-friendly, and the `rive:<id>` tags written into AE stay valid)."""

    def __init__(self, path=None):
        self.path = path
        self.map = {}
        if path and os.path.exists(path):
            try:
                self.map = json.load(open(path, encoding="utf-8"))
            except Exception:
                self.map = {}
        self.used = set()
        self.given = {}
        self.next_n = 1 + max([int(v.split(":")[1]) for v in self.map.values()] + [0])

    def key(self, *parts):
        """the same key always gives the same id (references); different keys never share one"""
        k = "/".join(str(p) for p in parts)
        v = self.given.get(k)
        if v is not None:
            return v
        v = self.map.get(k)
        if v is None or v in self.used:
            v = self.new()
            self.map[k] = v
        self.used.add(v)
        self.given[k] = v
        return v

    def new(self):
        while True:
            v = f"0:{self.next_n}"
            self.next_n += 1
            if v not in self.used and v not in self.map.values():
                self.used.add(v)
                return v

    def save(self):
        if self.path:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            keep = {k: v for k, v in self.map.items() if v in self.used}
            json.dump(keep, open(self.path, "w", encoding="utf-8"), indent=0, sort_keys=True)


# ------------------------------------------------------------------ report
class Report:
    KINDS = ("converted", "approx", "unsupported", "info")

    def __init__(self, title):
        self.title = title
        self.entries = []                 # (scope, kind, what, message)
        self.todo_effects = []            # effects that need a library entry (WGSL) or an AI rebuild
        self.fonts = {}
        self.footage = {}

    def add(self, scope, kind, what, msg):
        self.entries.append((clean(scope), kind, clean(what), clean(msg)))

    def counts(self):
        return collections.Counter(k for _, k, _, _ in self.entries)

    def markdown(self, extra=""):
        c = self.counts()
        out = [f"# ae2rml report — {self.title}", "",
               f"converted: {c['converted']} · approximated: {c['approx']} · unsupported: {c['unsupported']}", ""]
        if extra:
            out += [extra, ""]
        if self.fonts:
            out += ["## Fonts", "", "| AE font | file |", "|---|---|"]
            out += [f"| {k} | {v or '**not found — Helvetica stand-in (as AE)**'} |" for k, v in sorted(self.fonts.items())]
            out.append("")
        if self.footage:
            out += ["## Footage", "", "| item | resolved |", "|---|---|"]
            out += [f"| {k} | {v or '**missing**'} |" for k, v in sorted(self.footage.items())]
            out.append("")
        if self.todo_effects:
            out += ["## Effects to rebuild (no library entry)", "",
                    "Each line is an AE effect the converter could not express with Rive objects. It is kept as an "
                    "`<!-- ae: effect … -->` comment in scene.rml (rml2ae puts it back in AE) and listed in "
                    "`build/ae2rml/effects_todo.json` with its parameters, for a WGSL post-process to be written.", "",
                    "| comp | layer | effect | adjustment layer | animated params |", "|---|---|---|---|---|"]
            for e in self.todo_effects:
                out.append(f"| {e['comp']} | {e['layer']} | `{e['matchName']}` {e['name']} | {'yes' if e['adjustment'] else ''} | "
                           f"{', '.join(e['animated']) or ''} |")
            out.append("")
        seen = set()
        by_scope = collections.OrderedDict()
        for scope, kind, what, msg in self.entries:
            k = (scope, kind, what, msg)
            if k in seen:
                continue
            seen.add(k)
            by_scope.setdefault(scope, []).append((kind, what, msg))
        mark = {"converted": "✅", "approx": "≈", "unsupported": "✗", "info": "ℹ"}
        for scope, items in by_scope.items():
            out += [f"## {scope}", "", "| | element | note |", "|---|---|---|"]
            for kind, what, msg in items:
                out.append(f"| {mark.get(kind, kind)} | {what} | {msg} |")
            out.append("")
        return "\n".join(out)


# ------------------------------------------------------------------ footage lookup
def _parts(p):
    p = p.replace("\\", "/")
    p = re.sub(r"^[A-Za-z]:/", "/", p)
    return [x for x in p.split("/") if x]


def find_footage(path, aep_path, extra_dirs=()):
    """Resolve a footage path written on another machine (Windows drive letter, other volume): the longest tail of
    the path that exists under the .aep's folder or one of its ancestors (AE does the same relative search)."""
    if not path:
        return None
    if os.path.exists(path):
        return path
    parts = _parts(path)
    bases = []
    d = os.path.dirname(os.path.abspath(aep_path))
    while True:
        bases.append(d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    bases += list(extra_dirs)
    for n in range(len(parts), 0, -1):
        tail = parts[-n:]
        for b in bases:
            cand = os.path.join(b, *tail)
            if os.path.exists(cand):
                return cand
    # last resort: same file name next to the project (AE's "Collect files" layout)
    name = parts[-1] if parts else ""
    for b in bases[:2]:
        for root, _dirs, files in os.walk(b):
            if name in files:
                return os.path.join(root, name)
            if root.count(os.sep) - b.count(os.sep) > 3:
                break
    return None


# ------------------------------------------------------------------ fonts
if sys.platform.startswith("win"):
    _WINDIR = os.environ.get("WINDIR", r"C:\Windows")
    _LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    _ROAMING = os.environ.get("APPDATA", os.path.expanduser("~"))
    # system fonts, fonts installed for the current user only, Adobe Fonts (Creative Cloud) activated fonts
    FONT_DIRS = [os.path.join(_WINDIR, "Fonts"), os.path.join(_LOCAL, "Microsoft", "Windows", "Fonts"),
                 os.path.join(_ROAMING, "Adobe", "CoreSync", "plugins", "livetype", ".r"),
                 os.path.join(_ROAMING, "Adobe", "CoreSync", "plugins", "livetype", "r")]
else:
    FONT_DIRS = [os.path.expanduser("~/Library/Fonts"), "/Library/Fonts", "/System/Library/Fonts",
                 "/System/Library/Fonts/Supplemental",
                 os.path.expanduser("~/Library/Application Support/Adobe/CoreSync/plugins/livetype/.r"),
                 os.path.expanduser("~/Library/Application Support/Adobe/CoreSync/plugins/livetype/r")]
_FONT_INDEX = None


def _font_index():
    """PostScript name -> file, cached in ~/.cache/ae2rml/fonts.json (scanning the font name tables is slow)."""
    global _FONT_INDEX
    if _FONT_INDEX is not None:
        return _FONT_INDEX
    cache = os.path.expanduser("~/.cache/ae2rml/fonts.json")
    files = []
    for d in FONT_DIRS:
        if os.path.isdir(d):
            for root, _dirs, fs in os.walk(d):
                for f in fs:
                    if f.lower().endswith((".ttf", ".otf", ".ttc")) or (".r" in root and "." not in f):
                        files.append(os.path.join(root, f))
    stamp = len(files)
    try:
        c = json.load(open(cache, encoding="utf-8"))
        if c.get("stamp") == stamp:
            _FONT_INDEX = c["index"]
            return _FONT_INDEX
    except Exception:
        pass
    index = {}
    try:
        from fontTools.ttLib import TTFont, TTCollection
    except ImportError:
        _FONT_INDEX = {}
        return _FONT_INDEX
    for p in files:
        try:
            fonts = TTCollection(p, lazy=True).fonts if p.lower().endswith(".ttc") else [TTFont(p, lazy=True, fontNumber=0)]
            for i, t in enumerate(fonts):
                ps = t["name"].getDebugName(6)
                if ps and ps not in index:
                    index[ps] = [p, i]
        except Exception:
            continue
    _FONT_INDEX = index
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump({"stamp": stamp, "index": index}, open(cache, "w", encoding="utf-8"))
    except Exception:
        pass
    return index


def find_font(ps):
    """(path, index in a .ttc) of the font whose PostScript name is `ps`, or None."""
    if not ps:
        return None
    idx = _font_index()
    if ps in idx:
        return tuple(idx[ps])
    want = re.sub(r"[^a-z0-9]", "", ps.lower())
    for k, v in idx.items():
        if re.sub(r"[^a-z0-9]", "", k.lower()) == want:
            return tuple(v)
    return None


STAND_IN_TTC = (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf") if sys.platform.startswith("win")
                else "/System/Library/Fonts/Helvetica.ttc")


def copy_font(ps, out_dir, report):
    """Copy the font file into <out>/assets/ (a .ttc face is extracted to a .ttf) -> relative path."""
    os.makedirs(os.path.join(out_dir, "assets"), exist_ok=True)
    found = find_font(ps)
    if found:
        path, num = found
        if path.lower().endswith(".ttc"):
            try:
                from fontTools.ttLib import TTCollection
                rel = f"assets/{slug(ps)}.ttf"
                TTCollection(path).fonts[num].save(os.path.join(out_dir, rel))
                report.fonts[ps] = f"{path} (face {num})"
                return rel
            except Exception:
                found = None
        else:
            ext = os.path.splitext(path)[1] or ".otf"
            rel = f"assets/{slug(ps)}{ext.lower()}"
            shutil.copy(path, os.path.join(out_dir, rel))
            report.fonts[ps] = path
            return rel
    rel = f"assets/{slug(ps)}.ttf"
    report.fonts[ps] = None
    # a missing font: AE draws it in Helvetica (regular) on this Mac — measured on two projects, glyph overlap 0.85
    # against 0.79 for Arial and 0.25 for Myriad; the same stand-in makes Rive show what AE shows. A system font:
    # replace it with the real one before shipping the .riv
    try:
        from fontTools.ttLib import TTCollection
        TTCollection(STAND_IN_TTC).fonts[0].save(os.path.join(out_dir, rel))
        return rel
    except Exception:
        pass
    stand_in = os.path.join(ROOT, "figma2rml", "fonts", "Montserrat-Bold.ttf")
    if not os.path.exists(stand_in):
        stand_in = os.path.join(PKG, "tests", "cases", "features", "Montserrat-Bold.ttf")
    shutil.copy(stand_in, os.path.join(out_dir, rel))
    return rel


def font_ink(path, index=0):
    """ink(line, size, tracking) -> (x0, y0, x1, y1, advance) of one text line drawn from x = 0 on the baseline
    (y down): the glyph outlines' bounds, as AE's sourceRectAtTime measures text, or None if the font is unreadable"""
    try:
        from fontTools.ttLib import TTFont
        from fontTools.pens.boundsPen import BoundsPen
        t = TTFont(path, fontNumber=index, lazy=True)
        upm = float(t["head"].unitsPerEm)
        cmap, hm, gs = t.getBestCmap(), t["hmtx"], t.getGlyphSet()
    except Exception:
        return None
    cache = {}

    def glyph(ch):
        if ch not in cache:
            name = cmap.get(ord(ch)) or ".notdef"
            pen = BoundsPen(gs)
            try:
                gs[name].draw(pen)
                b = pen.bounds
            except Exception:
                b = None
            cache[ch] = (hm[name][0] if name in hm.metrics else upm * 0.5, b)
        return cache[ch]

    def ink(line, size, tracking=0.0):
        k, x, box = size / upm, 0.0, None
        for i, ch in enumerate(line):
            adv, b = glyph(ch)
            if b is not None:
                gb = (x + b[0] * k, -b[3] * k, x + b[2] * k, -b[1] * k)
                box = gb if box is None else (min(box[0], gb[0]), min(box[1], gb[1]), max(box[2], gb[2]), max(box[3], gb[3]))
            x += adv * k + (tracking * size / 1000.0 if i < len(line) - 1 else 0.0)
        return (box or (0.0, 0.0, 0.0, 0.0)) + (x,)
    return ink


def font_metrics(path):
    """(ascender, descender, measure(text, size)) of a font file, from hhea/hmtx."""
    try:
        from fontTools.ttLib import TTFont
        t = TTFont(path, lazy=True)
        upm = t["head"].unitsPerEm
        asc = t["hhea"].ascent / upm
        desc = -t["hhea"].descent / upm
        cmap, hm = t.getBestCmap(), t["hmtx"]
        fallback = cmap.get(ord("n")) or next(iter(cmap.values()))

        def measure_raw(txt, size):
            return sum(hm[cmap.get(ord(c), fallback)][0] for c in txt) / upm * size
        measure = measure_raw
        try:
            # shaped width (kerning applied): AE and Rive (HarfBuzz) both kern — summing raw advances made
            # "TEXT ON A PATH DEMO" in Arial 48 13 px too wide, a centred text on a path 7 px off (measured)
            import uharfbuzz as hb
            hfont = hb.Font(hb.Face(hb.Blob.from_file_path(path)))
            hupm = hfont.face.upem

            def measure(txt, size):
                if not txt:
                    return 0.0
                buf = hb.Buffer()
                buf.add_str(txt)
                buf.guess_segment_properties()
                hb.shape(hfont, buf, {"kern": True})
                return sum(p.x_advance for p in buf.glyph_positions) / hupm * size
        except Exception:
            pass
        return asc, desc, measure
    except Exception:
        return 0.93, 0.25, (lambda txt, size: len(txt) * size * 0.55)


# ---------------------------------------------------------------- PSD layers
def _packbits(data, n):
    out = bytearray()
    i = 0
    while len(out) < n and i < len(data):
        c = data[i]
        i += 1
        if c < 128:
            out += data[i:i + c + 1]
            i += c + 1
        elif c > 128:
            out += bytes([data[i]]) * (257 - c)
            i += 1
    return bytes(out[:n]).ljust(n, b"\0")


def psd_layers(path):
    """the layer records of a .psd / .psb: [{name, top, left, bottom, right, channels: {id: bytes-per-pixel plane},
    divider}] — only what an After Effects footage item needs (8 / 16-bit RGB). None if unreadable."""
    import struct
    import zlib
    with open(path, "rb") as fh:
        b = fh.read()
    if b[:4] != b"8BPS":
        return None
    ver, nch, h, w, depth, mode = struct.unpack(">H6xHIIHH", b[4:26])
    if mode != 3 or depth not in (8, 16):
        return None
    big = ver == 2
    p = 26
    p += 4 + struct.unpack(">I", b[p:p + 4])[0]                 # colour mode data
    p += 4 + struct.unpack(">I", b[p:p + 4])[0]                 # image resources
    L = 8 if big else 4
    p += L                                                      # layer & mask section length
    lil = struct.unpack(">Q" if big else ">I", b[p:p + L])[0]
    p += L
    if lil == 0:
        return []
    count = abs(struct.unpack(">h", b[p:p + 2])[0])
    p += 2
    recs = []
    for _ in range(count):
        top, left, bottom, right, nc = struct.unpack(">iiiiH", b[p:p + 18])
        p += 18
        chans = []
        for _c in range(nc):
            cid = struct.unpack(">h", b[p:p + 2])[0]
            ln = struct.unpack(">Q" if big else ">I", b[p + 2:p + 2 + L])[0]
            chans.append((cid, ln))
            p += 2 + L
        p += 12                                                 # 8BIM, blend key, opacity, clip, flags, filler
        extra = struct.unpack(">I", b[p:p + 4])[0]
        e0 = p + 4
        q = e0
        mlen = struct.unpack(">I", b[q:q + 4])[0]
        mask = b[q + 4:q + 4 + mlen]
        q += 4 + mlen
        q += 4 + struct.unpack(">I", b[q:q + 4])[0]            # blending ranges
        nlen = b[q]
        name = b[q + 1:q + 1 + nlen].decode("mac_roman", "replace")
        q += (1 + nlen + 3) // 4 * 4
        divider = False
        while q + 12 <= e0 + extra:
            sig, key = b[q:q + 4], b[q + 4:q + 8]
            if sig not in (b"8BIM", b"8B64"):
                break
            long_key = big and key in (b"LMsk", b"Lr16", b"Lr32", b"Layr", b"Mt16", b"Mt32", b"Mtrn", b"Alph",
                                        b"FMsk", b"lnk2", b"FEid", b"FXid", b"PxSD")
            dl = struct.unpack(">Q" if long_key else ">I", b[q + 8:q + 8 + (8 if long_key else 4)])[0]
            d0 = q + 8 + (8 if long_key else 4)
            if key == b"luni":
                n = struct.unpack(">I", b[d0:d0 + 4])[0]
                name = b[d0 + 4:d0 + 4 + 2 * n].decode("utf-16-be", "replace").rstrip("\0")
            elif key in (b"lsct", b"lsdk"):
                divider = struct.unpack(">I", b[d0:d0 + 4])[0] in (1, 2, 3)
            q = d0 + dl
        p = e0 + extra
        mrect = struct.unpack(">iiii", mask[:16]) if len(mask) >= 16 else None
        recs.append({"name": name, "top": top, "left": left, "bottom": bottom, "right": right, "chans": chans,
                     "divider": divider, "mask_rect": mrect})
    bpp = depth // 8
    for r in recs:
        planes = {}
        lw, lh = r["right"] - r["left"], r["bottom"] - r["top"]
        for cid, ln in r["chans"]:
            data = b[p:p + ln]
            p += ln
            cw, ch = lw, lh
            if cid in (-2, -3):
                if not r["mask_rect"]:
                    continue
                mt, ml, mb, mr = r["mask_rect"]
                cw, ch = mr - ml, mb - mt
            if cw <= 0 or ch <= 0 or len(data) < 2:
                continue
            comp = struct.unpack(">H", data[:2])[0]
            body = data[2:]
            n = cw * ch * bpp
            try:
                if comp == 0:
                    raw = body[:n]
                elif comp == 1:
                    cl = 4 if big else 2
                    counts = struct.unpack(">" + ("I" if big else "H") * ch, body[:cl * ch])
                    q, rows = cl * ch, []
                    for c in counts:
                        rows.append(_packbits(body[q:q + c], cw * bpp))
                        q += c
                    raw = b"".join(rows)
                elif comp in (2, 3):
                    raw = bytearray(zlib.decompress(body))
                    if comp == 3:                               # per-row delta prediction
                        for y in range(ch):
                            o = y * cw * bpp
                            if bpp == 1:
                                for x in range(1, cw):
                                    raw[o + x] = (raw[o + x] + raw[o + x - 1]) & 0xFF
                    raw = bytes(raw)
                else:
                    continue
            except Exception:
                continue
            if bpp == 2:
                raw = raw[0::2]                                 # 16-bit: keep the high byte
            planes[cid] = (cw, ch, raw)
        r["planes"] = planes
    return recs


def psd_layer_image(path, name, bounds=None, index=None):
    """one layer of a PSD as an RGBA PIL image at its own bounds, or None. Found by name + bounds (AE's
    file_attributes), then by name, then by AE's layer index (counted from the bottom, groups excluded)"""
    from PIL import Image
    try:
        recs = psd_layers(path)
    except Exception:
        return None
    if not recs:
        return None
    layers = [r for r in recs if not r["divider"]]

    def rect(r):
        return (r["top"], r["left"], r["bottom"], r["right"])
    pick = None
    if bounds is not None:
        pick = next((r for r in layers if r["name"] == name and rect(r) == tuple(bounds)), None) \
            or next((r for r in layers if rect(r) == tuple(bounds)), None)
    if pick is None:
        same = [r for r in layers if r["name"] == name]
        pick = same[0] if len(same) == 1 else None
    if pick is None and index is not None and 0 <= index < len(layers):
        pick = layers[index]
    if pick is None:
        return None
    lw, lh = pick["right"] - pick["left"], pick["bottom"] - pick["top"]
    pl = pick["planes"]
    if lw <= 0 or lh <= 0 or not all(c in pl for c in (0, 1, 2)):
        return None
    chans = [Image.frombytes("L", (lw, lh), pl[c][2]) for c in (0, 1, 2)]
    a = Image.frombytes("L", (lw, lh), pl[-1][2]) if -1 in pl else Image.new("L", (lw, lh), 255)
    return Image.merge("RGBA", chans + [a])
