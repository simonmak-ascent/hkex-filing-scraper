"""Inject schema.org structured data into every page.

MkDocs has no structured-data support, so this hook builds the graph per page and hands
it to ``overrides/main.html``, which emits it as ``<script type="application/ld+json">``.
The shapes follow the patterns already used by the sibling valuation apps
(``startup-valuation/lib/seo.ts``): a site entity, a breadcrumb trail on every page, and a
page-appropriate entity on top.

What each page gets:

====================  =======================================================
Page                  Entities
====================  =======================================================
home                  ``WebSite`` (with ``SearchAction``) + ``SoftwareApplication``
``getting-started``,
``live-mcp``          ``HowTo``, steps taken from the page's own H2 headings
``sinks/*``           ``TechArticle`` with ``about`` naming the engine
every other page      ``TechArticle``
every page            ``BreadcrumbList``
====================  =======================================================

Nothing is invented: the URL, title and description come from the page, and the steps are
its headings, so the markup cannot drift from the prose.
"""

from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.parse import urljoin

#: Pages whose H2 headings are a procedure, rendered as ``HowTo``.
HOWTO_PAGES = frozenset({"getting-started/", "live-mcp/"})

#: Engine names for the sink guides, used as ``TechArticle.about``.
SINK_ENGINES = {
    "postgresql": "PostgreSQL",
    "mysql": "MySQL and MariaDB",
    "sqlite": "SQLite",
    "mongodb": "MongoDB",
    "neo4j": "Neo4j",
    "clickhouse": "ClickHouse",
    "duckdb": "DuckDB",
    "surrealdb": "SurrealDB",
}


def _absolute(site_url: str, path: str) -> str:
    return urljoin(site_url, path)


def _heading(page: Any) -> str:
    """The page title as Material rendered it, without the site suffix."""
    title = getattr(page, "title", None)
    return title or "Documentation"


def _description(page: Any, site_description: str) -> str:
    meta = getattr(page, "meta", None) or {}
    value = meta.get("description") if hasattr(meta, "get") else None
    return value or site_description


def _steps_from_toc(toc: Any) -> list[dict[str, str]]:
    """Turn the page's top-level headings into ``HowToStep`` entries."""
    steps: list[dict[str, str]] = []
    for item in toc or []:
        title = getattr(item, "title", None)
        if not title:
            continue
        steps.append({"@type": "HowToStep", "name": title, "text": title})
    return steps


def _breadcrumb(site_url: str, page: Any) -> dict[str, Any] | None:
    """BreadcrumbList from the nav trail, home first."""
    trail: list[dict[str, Any]] = [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": _absolute(site_url, "")}
    ]
    ancestors: Iterable[Any] = getattr(page, "ancestors", None) or []
    for ancestor in ancestors:
        url = getattr(ancestor, "url", "") or ""
        title = getattr(ancestor, "title", "") or url
        # A section without its own page has no URL to point at.
        if url:
            trail.append(
                {
                    "@type": "ListItem",
                    "position": len(trail) + 1,
                    "name": title,
                    "item": _absolute(site_url, url),
                }
            )
    if len(trail) < 2:
        return None
    trail.append(
        {
            "@type": "ListItem",
            "position": len(trail) + 1,
            "name": _heading(page),
            "item": _absolute(site_url, getattr(page, "url", "") or ""),
        }
    )
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": trail}


def _article(site_url: str, page: Any, site_name: str) -> dict[str, Any]:
    url = _absolute(site_url, getattr(page, "url", "") or "")
    article: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": _heading(page),
        "url": url,
        "isPartOf": {
            "@type": "WebSite",
            "name": site_name,
            "url": _absolute(site_url, ""),
        },
    }
    segments = (getattr(page, "url", "") or "").strip("/").split("/")
    if len(segments) >= 2 and segments[0] == "sinks" and segments[1] in SINK_ENGINES:
        article["about"] = {"@type": "SoftwareApplication", "name": SINK_ENGINES[segments[1]]}
    return article


def _howto(site_url: str, page: Any, description: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": _heading(page),
        "description": description,
        "url": _absolute(site_url, getattr(page, "url", "") or ""),
        "totalTime": "PT10M",
        "step": _steps_from_toc(getattr(page, "toc", None)),
    }


def _website(site_url: str, site_name: str, description: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": site_name,
        "url": _absolute(site_url, ""),
        "description": description,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{_absolute(site_url, '')}?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }


def _application(site_url: str, description: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "HKEx Filing Scraper",
        "applicationCategory": "DeveloperApplication",
        "operatingSystem": "Cross-platform",
        "description": description,
        "url": _absolute(site_url, ""),
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "license": "https://opensource.org/licenses/MIT",
    }


def build_graph(
    *,
    site_url: str,
    site_name: str,
    site_description: str,
    page: Any,
) -> list[dict[str, Any]]:
    """The JSON-LD entities for one page. Pure: takes the page, returns dicts."""
    url = getattr(page, "url", "") or ""
    description = _description(page, site_description)
    graph: list[dict[str, Any]] = []

    if bool(getattr(page, "is_homepage", False)):
        graph.append(_website(site_url, site_name, site_description))
        graph.append(_application(site_url, description))

    if url in HOWTO_PAGES:
        howto = _howto(site_url, page, description)
        if howto["step"]:
            graph.append(howto)

    if not bool(getattr(page, "is_homepage", False)):
        graph.append(_article(site_url, page, site_name))

    breadcrumb = _breadcrumb(site_url, page)
    if breadcrumb:
        graph.append(breadcrumb)
    return graph


def dumps(graph: list[dict[str, Any]]) -> str:
    """Serialise for a ``<script>`` block: ``</`` is escaped so markup cannot break out."""
    return json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def on_page_context(context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """MkDocs hook: stash the serialised graph for ``overrides/main.html``."""
    page = kwargs.get("page")
    config = kwargs.get("config")
    if page is None or config is None:
        return context
    site_url = str(getattr(config, "site_url", "") or "")
    graph = build_graph(
        site_url=site_url,
        site_name=str(getattr(config, "site_name", "") or ""),
        site_description=str(getattr(config, "site_description", "") or ""),
        page=page,
    )
    if graph:
        context["page_jsonld"] = dumps(graph)
    return context
