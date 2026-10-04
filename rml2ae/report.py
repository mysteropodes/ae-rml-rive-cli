"""Conversion report: what was converted, approximated or ignored, per artboard, written as Markdown."""
import collections


class Report:
    def __init__(self, project):
        self.project = project
        self.entries = []          # (artboard, kind, element description, message)

    def add(self, artboard, kind, el, msg):
        desc = f"{el.tag} '{el.name}'" if el is not None else "-"
        self.entries.append((artboard, kind, desc, msg))

    def markdown(self, extra=""):
        by_ab = collections.OrderedDict()
        seen = collections.Counter()
        for ab, kind, desc, msg in self.entries:          # identical notes (same element, same message) are counted once
            k = (ab, kind, desc, msg)
            seen[k] += 1
            if seen[k] == 1:
                by_ab.setdefault(ab, []).append((kind, desc, msg))
        counts = collections.Counter(k for _, k, _, _ in self.entries)
        out = [f"# rml2ae report — {self.project}", "",
               f"converted: {counts['converted']} · approximated: {counts['approx']} · unsupported: {counts['unsupported']}", ""]
        if extra:
            out += [extra, ""]
        for ab, items in by_ab.items():
            out.append(f"## {ab}")
            out.append("")
            out.append("| | element | note |")
            out.append("|---|---|---|")
            mark = {"converted": "✅", "approx": "≈", "unsupported": "✗"}
            for kind, desc, msg in items:
                out.append(f"| {mark.get(kind, kind)} | {desc} | {msg} |")
            out.append("")
        return "\n".join(out)
