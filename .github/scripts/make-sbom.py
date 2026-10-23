#!/usr/bin/env python3
"""Build a CycloneDX SBOM from a pip-licenses inventory."""
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src, encoding="utf-8"))
components = [
    {
        "type": "library",
        "name": str(p.get("Name", "unknown")),
        "version": str(p.get("Version", "unknown")),
        "licenses": [{"expression": str(p.get("License", "UNKNOWN"))}],
    }
    for p in data
]
bom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1, "components": components}
with open(dst, "w", encoding="utf-8") as out:
    json.dump(bom, out, indent=2)
print(f"wrote {dst} with {len(components)} components")
