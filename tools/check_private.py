"""Fail if a tracked file carries private data: local user paths or e-mail addresses.

    python3 tools/check_private.py            # every file tracked by git
    python3 tools/check_private.py FILE ...   # only these files

Binary files (.aep, .riv, images, fonts) are scanned too: After Effects stores the absolute paths of footage in the
.aep, as plain bytes or UTF-16, so a project saved on a real machine can leak a user name or a client folder.
"""
import re
import subprocess
import sys

PATTERNS = [
    ("macOS user path", r"/Users/(?!Shared\b)[A-Za-z0-9._-]+"),
    ("macOS volume path", r"/Volumes/[A-Za-z0-9._ -]+"),
    ("Linux home path", r"/home/(?!user\b)[a-z_][a-z0-9_-]*/"),
    ("Windows user path", r"[A-Za-z]:\\+(?:Users|Documents and Settings)\\+[A-Za-z0-9._ -]+"),
    ("e-mail address", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
]
# generic or public addresses that are fine in a public repository
ALLOWED = re.compile(r"noreply@|@example\.(?:com|org)|git@github\.com|@anthropic\.com")
# this file describes the patterns, it does not contain private data
SKIP = {"tools/check_private.py"}


def strings(data):
    """Printable runs of the file, as ASCII/UTF-8 and as UTF-16 (little and big endian)."""
    yield data.decode("utf-8", "replace")
    for enc in ("utf-16-le", "utf-16-be"):
        for off in (0, 1):
            yield data[off:].decode(enc, "replace")


def png_text(data):
    """A PNG's metadata chunks only (tEXt / iTXt / zTXt): its compressed pixels are random bytes that can look like an
    e-mail address (a reference render failed on 'jmp@8.wy')."""
    import struct
    import zlib
    out, i = [], 8
    while i + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + n]
        if kind in (b"tEXt", b"iTXt"):
            out.append(body)
        elif kind == b"zTXt":
            try:
                out.append(body.split(b"\0", 1)[0] + b" " + zlib.decompress(body.split(b"\0", 1)[1][1:]))
            except Exception:
                out.append(body)
        i += 12 + n
    return b"\n".join(out)


def scan(path):
    try:
        data = open(path, "rb").read()
    except OSError:
        return []
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        data = png_text(data)
    hits = set()
    for text in strings(data):
        for label, pat in PATTERNS:
            for m in re.finditer(pat, text):
                if not ALLOWED.search(m.group(0)):
                    hits.add((label, m.group(0)[:80]))
    return sorted(hits)


def main(argv):
    files = argv or subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    bad = 0
    for f in files:
        if f in SKIP:
            continue
        for label, s in scan(f):
            print(f"{f}: {label}: {s}")
            bad += 1
    print(f"check_private: {bad} finding(s) in {len(files)} file(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
