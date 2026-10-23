#!/usr/bin/env python3
"""Fail when a production dependency carries a disallowed licence."""

import json
import re
import sys

DENY = re.compile(
    r"(?<!L)GPL(?:v?[23]|-|\b)|AGPL|GNU Affero|GNU General Public License|"
    r"SSPL|Server Side Public License|BUSL|Business Source License|Commons Clause|"
    r"CC-BY-NC|Elastic License|RSAL",
    re.IGNORECASE,
)
NOTICE = re.compile(r"LGPL|Lesser General Public|MPL|FSL|CC-BY-4.0", re.IGNORECASE)


def denied(expr: str) -> bool:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        expr = expr[1:-1].strip()
    ors = re.split(" OR ", expr, flags=re.IGNORECASE)
    if len(ors) > 1:
        return all(denied(part) for part in ors)
    ands = re.split(r"\s+AND\s+|;", expr, flags=re.IGNORECASE)
    if len(ands) > 1:
        return any(denied(part) for part in ands)
    return bool(DENY.search(expr))


def main(path: str) -> int:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        sys.stderr.write(f"{path}: expected a pip-licenses list\n")
        return 2
    denied_rows = []
    notices = []
    for package in data:
        licence = str(package.get("License", "UNKNOWN"))
        name = str(package.get("Name", "?"))
        if denied(licence):
            denied_rows.append(f"{licence}: {name}")
        elif NOTICE.search(licence):
            notices.append(f"{licence}: {name}")
    for line in notices:
        sys.stdout.write(f"notice: {line}\n")
    if denied_rows:
        sys.stderr.write("Disallowed production licences:\n")
        for line in denied_rows:
            sys.stderr.write(f"  {line}\n")
        return 1
    sys.stdout.write(f"license policy OK ({len(data)} packages)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
