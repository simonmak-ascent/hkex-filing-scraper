"""Structured data: the graph each page type gets.

Pure unit tests over `hooks/seo.py` — a stub page stands in for MkDocs' Page, so the
shapes are asserted without building the site.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

#: The hook is a MkDocs build artefact, not part of the wheel, so it is loaded by path.
#: The `wheel` CI job runs the suite against the installed package where it is absent.
HOOK = Path(__file__).resolve().parents[1] / "hooks" / "seo.py"
pytestmark = pytest.mark.skipif(
    not HOOK.exists(), reason="MkDocs build hook is not shipped in the wheel"
)


def _load_hook():
    spec = importlib.util.spec_from_file_location("seo_hook", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seo = _load_hook() if HOOK.exists() else None  # noqa: F841 - referenced by the tests

SITE = "https://example.test/docs/"
NAME = "HKEx Filing Scraper"
DESC = "Scrape HKEx filings into nine databases."


def page(
    url: str,
    title: str,
    *,
    description: str | None = None,
    home: bool = False,
    content: str = "",
) -> SimpleNamespace:
    meta = {"description": description} if description else {}
    return SimpleNamespace(
        url=url,
        title=title,
        meta=meta,
        is_homepage=home,
        content=content,
    )


def types(graph: list[dict]) -> list[str]:
    return [entity["@type"] for entity in graph]


def test_home_carries_website_and_application():
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description=DESC,
        page=page("", NAME, home=True),
    )

    assert types(graph) == ["WebSite", "SoftwareApplication"]
    website = graph[0]
    assert website["url"] == "https://example.test/docs/"
    assert website["potentialAction"]["target"]["urlTemplate"].endswith("?q={search_term_string}")
    assert graph[1]["offers"]["price"] == "0"


def test_a_sink_page_is_an_article_about_that_engine():
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description=DESC,
        page=page(
            "sinks/postgresql/",
            "PostgreSQL",
            description="Load HKEx filings into PostgreSQL.",
        ),
    )

    assert types(graph) == ["TechArticle", "BreadcrumbList"]
    assert graph[0]["about"] == {"@type": "SoftwareApplication", "name": "PostgreSQL"}
    assert graph[0]["headline"] == "PostgreSQL"

    trail = graph[1]["itemListElement"]
    assert [item["name"] for item in trail] == ["Home", "Sinks", "PostgreSQL"]
    assert [item["position"] for item in trail] == [1, 2, 3]


def test_getting_started_is_a_howto_whose_steps_are_its_headings():
    # As Material renders it: the permalink anchor carries `&para;`, and the fenced
    # code blocks contain `#` lines that are not headings.
    content = (
        '<h2 id="requirements">Requirements'
        '<a class="headerlink" href="#requirements">&para;</a></h2>'
        "<pre><code># PostgreSQL</code></pre>"
        '<h2 id="install">Install</h2>'
        '<h2 id="scrape">Scrape &#182;</h2>'
    )
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description=DESC,
        page=page("getting-started/", "Getting started", content=content),
    )

    assert "HowTo" in types(graph)
    howto = next(entity for entity in graph if entity["@type"] == "HowTo")
    assert [step["name"] for step in howto["step"]] == ["Requirements", "Install", "Scrape"]
    assert howto["url"] == "https://example.test/docs/getting-started/"


def test_a_howto_without_headings_is_not_emitted():
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description=DESC,
        page=page("getting-started/", "Getting Started"),
    )
    assert "HowTo" not in types(graph)


def test_description_falls_back_to_the_site_description():
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description=DESC,
        page=page("cli/", "CLI"),
    )
    howto_free = next(entity for entity in graph if entity["@type"] == "TechArticle")
    assert "description" not in howto_free  # TechArticle does not carry one
    assert seo._description(page("cli/", "CLI"), DESC) == DESC


def test_graphs_are_serialisable_and_cannot_break_out_of_the_script_tag():
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description="A </script> in a description",
        page=page("cli/", "CLI", description="has </script> inside"),
    )
    payload = seo.dumps(graph)

    assert "</script" not in payload.lower()
    assert json.loads(payload.replace("<\\/", "</")) == graph


def test_breadcrumb_titles_come_from_the_nav():
    nav = [
        SimpleNamespace(
            title=None,
            children=[SimpleNamespace(title="Sinks", children=None, url="sinks/")],
        ),
        SimpleNamespace(
            title="Sinks",
            children=[SimpleNamespace(title="PostgreSQL", url="sinks/postgresql/", children=None)],
        ),
    ]
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description=DESC,
        page=page("sinks/postgresql/", "PostgreSQL"),
        nav=nav,
    )
    trail = next(e for e in graph if e["@type"] == "BreadcrumbList")["itemListElement"]
    assert [item["name"] for item in trail] == ["Home", "Sinks", "PostgreSQL"]


def test_breadcrumb_falls_back_to_the_slug_without_a_nav():
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description=DESC,
        page=page("sinks/clickhouse/", "ClickHouse"),
    )
    trail = next(e for e in graph if e["@type"] == "BreadcrumbList")["itemListElement"]
    assert [item["name"] for item in trail] == ["Home", "Sinks", "ClickHouse"]


def test_the_home_page_has_no_breadcrumb():
    graph = seo.build_graph(
        site_url=SITE,
        site_name=NAME,
        site_description=DESC,
        page=page("", NAME, home=True),
    )
    assert "BreadcrumbList" not in types(graph)
