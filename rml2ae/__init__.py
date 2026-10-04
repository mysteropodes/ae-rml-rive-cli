"""rml2ae — Rive CLI project (RML) -> After Effects ExtendScript."""
import sys

# Windows: a console or pipe defaults to the ANSI code page (cp1252), which cannot print every character the reports
# use; write UTF-8 there instead (files are opened with encoding="utf-8" everywhere).
if sys.platform.startswith("win"):
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
