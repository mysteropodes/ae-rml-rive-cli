"""rml2ae/schema.json (generated from the rive-runtime headers) against the installed Rive CLI's `rive schema`.

    python tools/check_schema_cli.py [Type ...]

For every type of schema.json (or the ones named): the CLI must know it, with the same typeKey, and every property key
schema.json gives it must exist in the CLI with the same name. Runtime-only keys (computed by the runtime, never
authored, so absent from the CLI) are allowed. The CLI lists more than schema.json (editor-only properties, the
ArtboardProvider interface): that is not checked. Exit code 1 on a mismatch: regenerate schema.json with
rml2ae/tools/make_schema.py from a rive-runtime that matches the CLI.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_ONLY = {9: "xArtboard", 10: "yArtboard"}       # Node: the artboard-space position the runtime computes


def cli_type(t):
    r = subprocess.run(["rive", "schema", t, "--all"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=60, encoding="utf-8")
    m = re.search(r"^" + re.escape(t) + r"\s+\(typeKey (\d+)\)", r.stdout, re.M)
    if not m:
        return None
    keys = {int(k): n for n, k in re.findall(r"^\s{2}(\w+)\s+[A-Z]*\s+\S+\s+key (\d+)", r.stdout, re.M)}
    return int(m.group(1)), keys


def main(names):
    schema = json.load(open(os.path.join(ROOT, "rml2ae", "schema.json"), encoding="utf-8"))
    types = names or sorted(schema["types"])
    bad = []
    for t in types:
        d = schema["types"].get(t)
        if d is None:
            bad.append(f"{t}: not in schema.json")
            continue
        got = cli_type(t)
        if got is None:
            bad.append(f"{t}: unknown to the CLI")
            continue
        tk, keys = got
        if d["typeKey"] is not None and tk != d["typeKey"]:
            bad.append(f"{t}: typeKey {d['typeKey']} in schema.json, {tk} in the CLI")
        for p in d["properties"]:
            k, n = p["key"], p["name"]
            if k in keys:
                if keys[k] != n:
                    bad.append(f"{t}: key {k} is '{n}' in schema.json, '{keys[k]}' in the CLI")
            elif RUNTIME_ONLY.get(k) != n:
                bad.append(f"{t}: key {k} '{n}' unknown to the CLI")
    ver = subprocess.run(["rive", "--version"], capture_output=True, text=True).stdout.strip()
    for b in bad:
        print(b)
    print(f"check_schema_cli: {len(types)} type(s) against {ver}, {len(bad)} mismatch(es)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
