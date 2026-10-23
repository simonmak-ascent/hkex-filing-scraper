#!/usr/bin/env python3
"""Build a CycloneDX SBOM from a pip-licenses inventory."""

import json
import sys


def main(src: str, dst: str) -> None:
    with open(src, encoding="utf-8") as handle:
        data = json.load(handle)
    components = [
        {
            "type": "library",
            "name": str(package.get("Name", "unknown")),
            "version": str(package.get("Version", "unknown")),
            "licenses": [{"expression": str(package.get("License", "UNKNOWN"))}],
        }
        for package in data
    ]
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": components,
    }
    with open(dst, "w", encoding="utf-8") as out:
        json.dump(bom, out, indent=2)
    sys.stdout.write(f"wrote {dst} with {len(components)} components\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
