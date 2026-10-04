"""Regenerate rml2ae/schema.json from the MIT-licensed rive-runtime generated headers (include/rive/generated/*_base.hpp).

  python3 tools/make_schema.py ~/.cache/rive-runtime-generated   # folder of *_base.hpp (curl from github rive-app/rive-runtime)

Types: class name without the `Base` suffix; `extends` = the ancestor list of `isTypeOf`; properties = `<name>PropertyKey = N`
with the getter's C++ type mapped to the RML type names (double / uint / bool / String / Color / Id / Bytes).
"""
import json
import os
import re
import sys

CPP_TYPES = {"float": "double", "bool": "bool", "std::string": "String", "uint32_t": "uint", "uint16_t": "uint", "int32_t": "int",
             "ColorInt": "Color", "std::vector<uint8_t>": "Bytes", "Span<const uint8_t>": "Bytes"}


def parse(path):
    s = open(path, encoding="utf-8").read()
    m = re.search(r"class\s+(\w+)Base\s*:\s*public\s+(\w+)", s)
    if not m:
        return None
    name, parent = m.group(1), m.group(2)
    tk = re.search(r"static const uint16_t typeKey = (\d+);", s)
    anc = [a for a in re.findall(r"case (\w+)Base::typeKey:", s) if a != name]
    props = []
    for pname, key in re.findall(r"static const uint16_t (\w+)PropertyKey = (\d+);", s):
        g = re.search(r"(?:inline|virtual)\s+([\w:<>\s]+?)\s+" + re.escape(pname) + r"\(\)\s*const", s)
        ctype = g.group(1).strip() if g else ""
        if ctype in ("", "const std::string&", "std::string"):
            ctype = "std::string" if "std::string" in ctype or ("m_" + pname[:1].upper() + pname[1:]) in s and "std::string" in s else ctype
        t = CPP_TYPES.get(ctype.replace("const ", "").replace("&", "").strip(), None)
        if t is None and (pname in ("bytes",) or pname.endswith("Bytes") or pname.endswith("Ids")):
            t = "Bytes"                                   # Span<const uint8_t> / std::vector getters, not always inline
        if t is None:
            if "Id" in pname[-2:] or pname.endswith("Id"):
                t = "Id"
            elif "color" in pname.lower():
                t = "Color"
            else:
                t = "double" if ctype == "" else ctype
        props.append({"name": pname, "owner": name, "type": t, "key": int(key)})
    return {"type": name, "typeKey": int(tk.group(1)) if tk else None, "extends": anc, "parent": parent, "properties": props}


def main(folder):
    types = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith("_base.hpp"):
            d = parse(os.path.join(folder, f))
            if d:
                types[d["type"]] = d
    # inherited properties (the CLI schema lists them on every type): flatten by walking `extends`
    keys = {}
    for t, d in types.items():
        for p in d["properties"]:
            keys.setdefault(p["key"], (p["owner"], p["name"], p["type"]))
    for t, d in types.items():
        own = {p["key"] for p in d["properties"]}
        for a in d["extends"]:
            for p in types.get(a, {"properties": []})["properties"]:
                if p["key"] not in own:
                    d["properties"].append(dict(p))
                    own.add(p["key"])
    out = {"source": "rive-app/rive-runtime include/rive/generated (MIT)", "types": types, "keys": {str(k): list(v) for k, v in keys.items()}}
    here = os.path.dirname(os.path.abspath(__file__))
    json.dump(out, open(os.path.join(here, "..", "schema.json"), "w"), indent=0)
    print(len(types), "types,", len(keys), "property keys")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.cache/rive-runtime-generated"))
