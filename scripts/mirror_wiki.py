#!/usr/bin/env python3
"""Generate a GitHub wiki mirror from the mkdocs sources under ``docs/``.

The docs site (mkdocs) is the source of truth. This script renders the same pages into
GitHub wiki page names, rewrites relative links into wiki links, and emits a sidebar and
footer. It is deterministic: run it twice and the output is identical, so the wiki only
changes when the docs change.

Usage::

    python scripts/mirror_wiki.py --out build/wiki
    python scripts/mirror_wiki.py --check          # verify every doc has a page

Only the standard library is used, so it runs anywhere Python 3.10+ is available.
"""

from __future__ import annotations

import argparse
import pathlib
import posixpath
import re
import shutil
import sys

# Pages, in sidebar order: (source path relative to docs/, wiki page name).
# A new doc under docs/ must be added here or ``--check`` fails.
HOME = "index.md"
HOME_PAGE = "Home"

SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Getting started",
        [
            ("getting-started.md", "Getting-Started"),
            ("news.md", "Whats-New"),
        ],
    ),
    (
        "Sinks",
        [
            ("sinks/README.md", "Sinks"),
            ("sinks/postgresql.md", "PostgreSQL"),
            ("sinks/mysql.md", "MySQL-and-MariaDB"),
            ("sinks/sqlite.md", "SQLite"),
            ("sinks/mongodb.md", "MongoDB"),
            ("sinks/neo4j.md", "Neo4j"),
            ("sinks/clickhouse.md", "ClickHouse"),
            ("sinks/duckdb.md", "DuckDB"),
            ("sinks/surrealdb.md", "SurrealDB"),
        ],
    ),
    (
        "Reference",
        [
            ("configuration.md", "Configuration"),
            ("cli.md", "CLI"),
            ("mcp.md", "MCP"),
            ("live-mcp.md", "Live-MCP-Gateway"),
            ("ai-agents.md", "AI-Agent-Support"),
            ("architecture.md", "Architecture"),
            ("troubleshooting.md", "Troubleshooting"),
            ("testing.md", "Testing"),
        ],
    ),
    (
        "Project",
        [
            ("de-risking.md", "De-risking"),
            ("roadmap.md", "Roadmap"),
            ("legal.md", "Legal"),
            ("upgrading.md", "Upgrading"),
            ("releasing.md", "Releasing"),
            ("release-automation.md", "Release-Automation"),
            ("STYLE.md", "Style"),
        ],
    ),
    (
        "Decisions",
        [
            ("adr/0001-versioning-and-release-automation.md", "ADR-0001-Versioning"),
            ("adr/0002-multi-sink-architecture.md", "ADR-0002-Multi-Sink"),
            ("adr/0003-sink-support-policy.md", "ADR-0003-Support-Policy"),
            ("adr/0004-publish-to-pypi-trusted-publishing.md", "ADR-0004-Publish-To-PyPI"),
        ],
    ),
]

PAGE_MAP: dict[str, str] = {source: page for _, pages in SECTIONS for source, page in pages}

LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def iter_docs(docs_dir: pathlib.Path) -> list[str]:
    """Every markdown file under *docs_dir*, as posix paths relative to it."""
    return sorted(p.relative_to(docs_dir).as_posix() for p in docs_dir.rglob("*.md"))


def missing_from_map(docs_dir: pathlib.Path) -> list[str]:
    """Docs that are not mirrored (or not the home page)."""
    return [rel for rel in iter_docs(docs_dir) if rel != HOME and rel not in PAGE_MAP]


def extra_in_map(docs_dir: pathlib.Path) -> list[str]:
    """Page-map entries with no source file."""
    return [rel for rel in PAGE_MAP if not (docs_dir / rel).exists()]


def _repo_url(repo: str, branch: str, root_rel: str, raw: bool) -> str:
    host = "raw.githubusercontent.com" if raw else "github.com"
    kind = "" if raw else "blob/"
    return f"https://{host}/{repo}/{kind}{branch}/{root_rel}"


def _wiki_link(page: str, fragment: str, label: str) -> str:
    target = f"{page}#{fragment}" if fragment else page
    if label == page or label == target:
        return f"[[{target}]]"
    return f"[[{target}|{label}]]"


def rewrite_links(
    text: str,
    source_rel: str,
    docs_dir: pathlib.Path,
    root: pathlib.Path,
    repo: str,
    branch: str,
) -> str:
    """Rewrite relative markdown links/images so they work inside the wiki.

    - a link to a mirrored doc becomes a wiki link ``[[Page]]``;
    - a link to another repo file becomes a GitHub URL;
    - a link to a binary asset becomes a raw URL;
    - absolute URLs and bare fragments are left untouched.
    """
    parent = posixpath.dirname(source_rel)
    docs_prefix = docs_dir.relative_to(root).as_posix()

    def resolve(target: str) -> tuple[str, str] | None:
        fragment = ""
        if "#" in target:
            target, fragment = target.split("#", 1)
        if not target:
            return None
        if "://" in target or target.startswith(("mailto:", "#")):
            return None
        normalized = posixpath.normpath(posixpath.join(parent, target))
        return normalized, fragment

    def replace_link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        resolved = resolve(target)
        if resolved is None:
            return match.group(0)
        normalized, fragment = resolved
        if not normalized.startswith("..") and normalized in PAGE_MAP:
            return _wiki_link(PAGE_MAP[normalized], fragment, label)
        root_rel = posixpath.normpath(posixpath.join(docs_prefix, normalized))
        if root_rel.startswith(".."):
            return match.group(0)
        if not (root / root_rel).exists():
            return match.group(0)
        kind = "raw" if not root_rel.endswith(".md") else "blob"
        suffix = f"#{fragment}" if fragment else ""
        return f"[{label}]({_repo_url(repo, branch, root_rel, raw=(kind == 'raw'))}{suffix})"

    def replace_image(match: re.Match[str]) -> str:
        alt, target = match.group(1), match.group(2)
        if "://" in target:
            return match.group(0)
        normalized = posixpath.normpath(posixpath.join(parent, target))
        root_rel = posixpath.normpath(posixpath.join(docs_prefix, normalized))
        if root_rel.startswith("..") or not (root / root_rel).exists():
            return match.group(0)
        return f"![{alt}]({_repo_url(repo, branch, root_rel, raw=True)})"

    text = LINK_RE.sub(replace_link, text)
    return IMAGE_RE.sub(replace_image, text)


def render_sidebar() -> str:
    lines = ["# HKEx Filing Scraper", "", f"- [[{HOME_PAGE}]]", ""]
    for title, pages in SECTIONS:
        lines.append(f"### {title}")
        lines.append("")
        for source, page in pages:
            lines.append(f"- [[{page}]]")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_footer(repo: str) -> str:
    return (
        "---\n\n"
        f"Generated from the [{repo}](https://github.com/{repo}) docs by "
        "`scripts/mirror_wiki.py`. Do not edit wiki pages by hand — edit `docs/` in the "
        "repository and the mirror will refresh.\n"
    )


def strip_front_matter(text: str) -> str:
    """Remove a leading YAML front-matter block (``---`` … ``---``).

    The docs site reads the block for per-page meta descriptions; the GitHub wiki has no
    use for it and would render it as visible text.
    """
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            return "".join(lines[index + 1 :]).lstrip("\n")
    return text


def build(
    docs_dir: pathlib.Path,
    root: pathlib.Path,
    out_dir: pathlib.Path,
    repo: str,
    branch: str,
) -> int:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    (out_dir / "_Sidebar.md").write_text(render_sidebar(), encoding="utf-8")
    (out_dir / "_Footer.md").write_text(render_footer(repo), encoding="utf-8")

    for source, page in [(HOME, HOME_PAGE)] + [pair for _, pages in SECTIONS for pair in pages]:
        body = strip_front_matter((docs_dir / source).read_text(encoding="utf-8"))
        body = rewrite_links(body, source, docs_dir, root, repo, branch)
        if source != HOME:
            body = f"{body.rstrip()}\n\n---\n\n[[{HOME_PAGE}|Home]]\n"
        (out_dir / f"{page}.md").write_text(body, encoding="utf-8")

    return len(PAGE_MAP) + 1  # + the home page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--out", default="build/wiki")
    parser.add_argument("--repo", default="simonmak-ascent/hkex-filing-scraper")
    parser.add_argument("--branch", default="main")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify that every doc has a wiki page, then exit",
    )
    args = parser.parse_args(argv)

    root = pathlib.Path.cwd()
    docs_dir = root / args.docs_dir

    missing = missing_from_map(docs_dir)
    extra = extra_in_map(docs_dir)
    if missing or extra:
        for rel in missing:
            print(f"error: {args.docs_dir}/{rel} has no wiki page", file=sys.stderr)
        for rel in extra:
            print(f"error: wiki page map lists missing {args.docs_dir}/{rel}", file=sys.stderr)
        print(
            "Add or remove the entry in scripts/mirror_wiki.py:SECTIONS.",
            file=sys.stderr,
        )
        return 1

    if args.check:
        print(f"ok: {len(PAGE_MAP)} docs mapped, plus the home page")
        return 0

    count = build(docs_dir, root, root / args.out, args.repo, args.branch)
    print(f"wrote {count} wiki pages to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
