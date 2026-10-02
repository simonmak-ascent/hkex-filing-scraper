"""Guard: user-facing docs and config must agree with the code.

This is the control for risk R4 (docs ↔ code drift). It fails a PR when a sink is
added, removed, or renamed without updating the docs, templates, or the env
template. It also checks the MCP tool catalogs (stdio and live), the live-gateway
endpoint, the CLI flags, and the hand-maintained ``docs/llms.txt`` index.
"""

from __future__ import annotations

import json
import pathlib
import re
import struct

import pytest

from hkex_scraper import sinks

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# Sinks whose primary guide lives elsewhere (shared or top-level).
GUIDE_OVERRIDES = {
    "postgres": "docs/sinks/postgresql.md",  # page slug matches the docs URL
    "mariadb": "docs/sinks/mysql.md",  # mysql.md documents both engines
}

# The documented order: sinks sorted by developer usage (Stack Overflow Developer Survey
# 2025), then the remaining engines by DB-Engines popularity. Kept here as a literal so that
# adding, removing, or renaming a sink fails until the docs and this list are updated together.
# It must match `sinks/registry.py:POPULARITY_ORDER`.
POPULARITY_ORDER = [
    "postgres",
    "mysql",
    "sqlite",
    "mongodb",
    "mariadb",
    "neo4j",
    "clickhouse",
    "duckdb",
    "surrealdb",
]
DOCUMENTED_ID_LIST = ", ".join(f"`{sid}`" for sid in POPULARITY_ORDER)

# Files that must list every sink id, in order, on the DATABASE_TARGET reference row.
CANONICAL_LIST_FILES = (
    "README.md",
    "docs/configuration.md",
    "docs/cli.md",
)


class TestSinkIdsInDocs:
    def test_documented_order_matches_the_registry(self):
        assert POPULARITY_ORDER == sinks.known_ids(), (
            "POPULARITY_ORDER is out of step with sinks/registry.py:POPULARITY_ORDER; "
            "update the registry, the docs reference rows, and this list together"
        )

    def test_canonical_id_list_is_literal_in_reference_rows(self):
        for rel in CANONICAL_LIST_FILES:
            assert DOCUMENTED_ID_LIST in _read(rel), (
                f"{rel} must list every sink id in documented order on the DATABASE_TARGET row"
            )

    def test_readme_lists_every_sink(self):
        readme = _read("README.md")
        for sink_id in sinks.known_ids():
            assert f"`{sink_id}`" in readme, f"README does not mention sink `{sink_id}`"

    def test_cli_doc_lists_every_sink(self):
        cli = _read("docs/cli.md")
        for sink_id in sinks.known_ids():
            assert f"`{sink_id}`" in cli, f"docs/cli.md does not mention sink `{sink_id}`"

    def test_bug_template_lists_every_sink(self):
        template = _read(".github/ISSUE_TEMPLATE/bug_report.yml")
        for sink_id in sinks.known_ids():
            assert f"- {sink_id}" in template, f"bug_report.yml missing option {sink_id}"

    def test_configuration_doc_lists_every_sink(self):
        configuration = _read("docs/configuration.md")
        for sink_id in sinks.known_ids():
            assert f"`{sink_id}`" in configuration, f"configuration.md missing {sink_id}"

    def test_no_stale_both_or_default_wording(self):
        for rel in ("docs/configuration.md", "docs/cli.md", "README.md"):
            text = _read(rel)
            assert not re.search(r"DATABASE_TARGET=both", text), f"{rel} still documents 'both'"
            assert "surrealdb (default)" not in text, f"{rel} still calls surrealdb the default"


class TestBackendGuides:
    def test_every_sink_has_a_guide(self):
        for sink_id in sinks.known_ids():
            rel = GUIDE_OVERRIDES.get(sink_id, f"docs/sinks/{sink_id}.md")
            assert (ROOT / rel).exists(), f"missing guide for `{sink_id}` (looked for {rel})"

    def test_matrix_lists_every_sink(self):
        matrix = _read("docs/sinks/README.md")
        for sink_id in sinks.known_ids():
            assert f"`{sink_id}`" in matrix, f"matrix does not list `{sink_id}`"


class TestExtrasAlignment:
    def _pyproject_extras(self) -> set[str]:
        # Parse the table text rather than tomllib (stdlib only from Python 3.11).
        text = _read("pyproject.toml")
        section = text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
        extras = set()
        for line in section.splitlines():
            match = re.match(r"^([A-Za-z0-9_-]+)\s*=", line)
            if match:
                extras.add(match.group(1))
        return extras

    def test_every_extra_is_documented_in_readme(self):
        readme = _read("README.md")
        for extra in self._pyproject_extras():
            assert f"`{extra}`" in readme, f"README does not document extra `{extra}`"

    def test_every_sink_extra_exists_in_pyproject(self):
        extras = self._pyproject_extras()
        for sink_id, spec in sinks.SINKS.items():
            if spec.extra is not None:
                assert spec.extra in extras, (
                    f"sink `{sink_id}` declares extra `{spec.extra}` not present in pyproject"
                )


class TestEnvTemplate:
    def test_env_example_exists_and_covers_sinks(self):
        path = ROOT / ".env.example"
        if not path.exists():
            pytest.skip(".env.example not present")
        text = path.read_text(encoding="utf-8")
        for sink_id in sinks.known_ids():
            assert f"`{sink_id}`" in text or sink_id in text, (
                f".env.example does not mention sink `{sink_id}`"
            )
        assert "both" not in text, ".env.example still documents the removed `both` alias"
        assert "DATABASE_TARGET=surrealdb" not in text, ".env.example still sets a silent default"


def _wiki_module():
    import importlib.util

    path = ROOT / "scripts/mirror_wiki.py"
    spec = importlib.util.spec_from_file_location("mirror_wiki", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWikiMirror:
    """The wiki mirror must stay complete as docs are added or removed."""

    def test_every_doc_has_a_wiki_page(self):
        module = _wiki_module()
        assert module.missing_from_map(ROOT / "docs") == [], (
            "a doc under docs/ is not mapped in scripts/mirror_wiki.py:SECTIONS"
        )

    def test_wiki_page_map_has_no_dangling_entries(self):
        module = _wiki_module()
        assert module.extra_in_map(ROOT / "docs") == [], (
            "scripts/mirror_wiki.py maps a doc that no longer exists"
        )

    def test_sidebar_is_generated(self):
        module = _wiki_module()
        sidebar = module.render_sidebar()
        for page in module.PAGE_MAP.values():
            assert f"[[{page}]]" in sidebar, f"sidebar is missing {page}"


class TestDocumentationStyle:
    """Guard the house style: US English and one canonical term (docs/STYLE.md)."""

    STYLE_FILES = (
        "README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        "GOVERNANCE.md",
        "CODE_OF_CONDUCT.md",
        "docs/index.md",
        "docs/getting-started.md",
        "docs/configuration.md",
        "docs/cli.md",
        "docs/testing.md",
        "docs/troubleshooting.md",
        "docs/upgrading.md",
        "docs/sinks/postgresql.md",
        "docs/sinks/surrealdb.md",
        "docs/sinks/README.md",
    )

    def test_style_guide_exists(self):
        assert (ROOT / "docs/STYLE.md").exists(), "docs/STYLE.md is missing"

    def test_us_spelling(self):
        offenders = [rel for rel in self.STYLE_FILES if "licence" in _read(rel)]
        assert not offenders, f"UK spelling 'licence' found in: {', '.join(offenders)}"

    def test_no_backend_synonym_in_prose(self):
        # The canonical term is "sink"; "backend" survives only in the build-backend
        # sense (hatchling), which does not appear in these files.
        offenders = [rel for rel in self.STYLE_FILES if "backend" in _read(rel).lower()]
        assert not offenders, f"use 'sink' instead of 'backend' in: {', '.join(offenders)}"


# --- MCP, live gateway, CLI, and the LLM index ---------------------------------
def _site_domain() -> str:
    for line in _read("mkdocs.yml").splitlines():
        if line.startswith("site_url:"):
            return line.split(":", 1)[1].strip().rstrip("/")
    raise AssertionError("mkdocs.yml has no site_url")


class TestLiveGatewayDocs:
    """The live-gateway docs must match the deployed endpoint and the tool catalog."""

    def test_documented_endpoint_matches_the_docs_domain(self):
        domain = _site_domain()
        for rel in ("docs/live-mcp.md", "docs/ai-agents.md", "README.md"):
            assert f"{domain}/api/mcp" in _read(rel), f"{rel} must use the /api/mcp endpoint"

    def test_live_tool_catalog_matches_the_docs(self):
        from hkex_scraper import live_mcp

        names = [tool["name"] for tool in live_mcp.TOOL_SCHEMAS]
        assert names == ["get_server_info", "search_filings", "list_filing_facets", "get_filing"]
        for rel in ("docs/live-mcp.md", "docs/ai-agents.md"):
            doc = _read(rel)
            missing = [name for name in names if f"`{name}`" not in doc]
            assert not missing, f"{rel} does not document live tools: {missing}"

    def test_api_entry_point_uses_the_shared_transport(self):
        assert "handle_jsonrpc" in _read("api/mcp.py"), (
            "api/mcp.py must delegate to live_mcp.handle_jsonrpc so the wire format stays shared"
        )


class TestMcpCatalogDocs:
    """docs/mcp.md must list every stdio MCP tool."""

    def test_tool_names_match_the_catalog(self):
        from hkex_scraper import mcp_server

        names = [tool.__name__ for tool in mcp_server.TOOLS]
        doc = _read("docs/mcp.md")
        missing = [name for name in names if f"`{name}`" not in doc]
        assert not missing, f"docs/mcp.md does not list MCP tools: {missing}"


class TestCliFlagsDocs:
    """Every argparse flag must be documented in docs/cli.md, and vice versa."""

    def test_flags_match_the_cli_doc(self):
        source_flags = set(re.findall(r'"(--[a-z][a-z-]*)"', _read("src/hkex_scraper/main.py")))
        assert source_flags, "no CLI flags found in main.py"
        doc = _read("docs/cli.md")
        missing = sorted(f for f in source_flags if f"`{f}" not in doc)
        assert not missing, f"docs/cli.md does not document flags: {missing}"
        documented = set(re.findall(r"`(--[a-z][a-z-]*)", doc))
        unknown = sorted(documented - source_flags)
        assert not unknown, f"docs/cli.md documents flags that main.py does not define: {unknown}"


class TestLlmsIndex:
    """docs/llms.txt is hand-maintained, so its links must resolve to real pages."""

    def test_links_resolve_to_existing_pages(self):
        domain = _site_domain()
        urls = re.findall(r"\((https://[^)\s]+)\)", _read("docs/llms.txt"))
        assert urls, "docs/llms.txt has no links"
        generated = {"llms.txt", "llms-full.txt"}
        for url in urls:
            assert url.startswith(f"{domain}/"), f"llms.txt link is outside the docs domain: {url}"
            slug = url[len(domain) + 1 :].strip("/")
            if slug in generated:
                continue
            candidates = [
                ROOT / "docs" / f"{slug}.md",
                ROOT / "docs" / slug / "README.md",
            ]
            if slug == "":
                candidates.append(ROOT / "docs" / "index.md")
            assert any(candidate.exists() for candidate in candidates), (
                f"docs/llms.txt links to a page that does not exist: {url}"
            )


# --- Brand assets --------------------------------------------------------------
def _png_size(rel: str) -> tuple[int, int]:
    data = (ROOT / rel).read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{rel} is not a PNG"
    return struct.unpack(">II", data[16:24])


class TestBrandAssets:
    """The committed artwork must match the sizes the site and GitHub advertise."""

    def test_social_preview_size_matches_the_og_meta(self):
        assert _png_size("docs/social_preview.png") == (1280, 640)
        head = _read("overrides/main.html")
        assert 'content="1280"' in head and 'content="640"' in head

    def test_mcp_diagram_png_size(self):
        assert _png_size("docs/assets/mcp.png") == (1120, 420)

    def test_brand_sources_exist(self):
        for name in ("social.svg", "mcp.svg", "banner.svg", "favicon.svg", "demo.svg"):
            assert (ROOT / "docs" / "assets" / name).exists(), f"docs/assets/{name} is missing"

    def test_readme_images_use_absolute_urls(self):
        readme = _read("README.md")
        sources = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", readme)
        assert sources, "README has no images"
        relative = [src for src in sources if not src.startswith("https://")]
        assert not relative, f"README images must be absolute URLs: {relative}"

    def test_readme_shows_the_mcp_diagram(self):
        assert "docs/assets/mcp.png" in _read("README.md")


class TestMcpRegistryManifest:
    """`server.json` publishes the gateway; it must match the docs and the README marker."""

    def _manifest(self) -> dict:
        return json.loads(_read("server.json"))

    def test_remote_matches_the_documented_endpoint(self):
        manifest = self._manifest()
        assert manifest["name"] == "io.github.simonmak-ascent/hkex-filings"
        assert manifest["description"].endswith(".")
        assert f"{_site_domain()}/api/mcp" in [r["url"] for r in manifest["remotes"]]

    def test_package_entry_points_at_this_distribution(self):
        packages = self._manifest()["packages"]
        assert any(p["identifier"] == "hkex-filing-scraper" for p in packages)

    def test_readme_carries_the_registry_marker(self):
        # The registry verifies PyPI ownership by finding this string in the README.
        assert f"mcp-name: {self._manifest()['name']}" in _read("README.md")
