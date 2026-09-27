"""Offline unit tests for the live HKEx MCP gateway (no network, no database).

The live fetch path is covered by ``tests/test_api_contract.py``; these tests cover the
gateway's own logic — date handling, the SSRF allowlist, response bounds, schema wiring,
and result shaping — with the network layer stubbed.
"""

from __future__ import annotations

from datetime import date

import pytest

from hkex_scraper import extractor, live_mcp


class TestDateParsing:
    def test_iso(self):
        assert live_mcp._parse_date("2026-09-12", "from_date") == date(2026, 9, 12)

    def test_day_month_year(self):
        assert live_mcp._parse_date("12/09/2026", "from_date") == date(2026, 9, 12)

    @pytest.mark.parametrize("value", ["", "nope", "2026/09/12", "13-09-2026"])
    def test_invalid(self, value):
        with pytest.raises(live_mcp.McpError):
            live_mcp._parse_date(value, "from_date")


class TestSsrGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0211/a.pdf",
            "https://hkexnews.hk/a.pdf",
            "http://www1.hkexnews.hk/a.htm",
        ],
    )
    def test_allows_hkex_hosts(self, url):
        assert live_mcp.is_allowed_document_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.com/a.pdf",
            "https://hkexnews.hk.evil.example.com/a.pdf",
            "file:///etc/passwd",
            "ftp://www1.hkexnews.hk/a.pdf",
            "not-a-url",
            "",
        ],
    )
    def test_rejects_other_hosts(self, url):
        assert live_mcp.is_allowed_document_url(url) is False


class TestBounds:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0, 1), (10, 10), (10_000, live_mcp.MAX_MAX_RESULTS), ("x", 1)],
    )
    def test_clamp(self, value, expected):
        assert live_mcp._clamp(value, 1, live_mcp.MAX_MAX_RESULTS) == expected

    def test_truncate_text(self):
        text, truncated = live_mcp._truncate_text("x" * 10, 4)
        assert text == "xxxx" and truncated is True
        text, truncated = live_mcp._truncate_text("abc", 4)
        assert text == "abc" and truncated is False

    def test_cap_tables(self):
        tables, omitted = live_mcp._cap_tables([1, 2, 3, 4], 2)
        assert tables == [1, 2] and omitted == 2
        tables, omitted = live_mcp._cap_tables([1], 2)
        assert tables == [1] and omitted == 0


class TestSchemas:
    def test_names_match_tools(self):
        assert [schema["name"] for schema in live_mcp.TOOL_SCHEMAS] == [
            tool.__name__ for tool in live_mcp.TOOLS
        ]

    def test_required_fields(self):
        schemas = {schema["name"]: schema for schema in live_mcp.TOOL_SCHEMAS}
        assert schemas["search_filings"]["inputSchema"]["required"] == ["from_date", "to_date"]
        assert schemas["list_filing_facets"]["inputSchema"]["required"] == ["from_date", "to_date"]
        assert schemas["get_filing"]["inputSchema"]["required"] == ["link"]
        assert schemas["get_server_info"]["inputSchema"]["required"] == []

    def test_search_filings_exposes_advanced_filters(self):
        schemas = {schema["name"]: schema for schema in live_mcp.TOOL_SCHEMAS}
        props = schemas["search_filings"]["inputSchema"]["properties"]
        for name in ("stock_code", "title_query", "document_type", "category", "stock_name"):
            assert name in props


class TestHandleTool:
    def test_unknown_tool(self):
        with pytest.raises(live_mcp.McpError):
            live_mcp.handle_tool("does_not_exist", {})

    def test_get_server_info(self):
        info = live_mcp.handle_tool("get_server_info", {})
        assert info["live"] is True
        assert info["transport"] == "streamable-http"
        assert info["storage"] == "none"
        assert info["read_only"] is True
        assert "extraction" in info


def _stub_records(monkeypatch, records, total=None):
    """Stub the network so ``_fetch_window`` returns ``records``."""
    monkeypatch.setattr(live_mcp.http, "make_session", lambda: object())
    monkeypatch.setattr(
        live_mcp.api,
        "fetch_chunk_via_api",
        lambda *a, **k: (records, total if total is not None else len(records)),
    )


_RICH_RECORDS = [
    {
        "stockCode": "01461",
        "stockName": "ZHONGTAI FUTURES",
        "title": "Dividend Notice",
        "fileType": "PDF",
        "category": "Announcements and Notices - [Dividend]",
    },
    {
        "stockCode": "00005",
        "stockName": "HSBC HOLDINGS",
        "title": "Annual Report 2025",
        "fileType": "HTML",
        "category": "Financial Statements - [Annual Report]",
    },
]


class TestSearchFilings:
    def test_window_too_wide(self):
        with pytest.raises(live_mcp.McpError, match="window too wide"):
            live_mcp._tool_search_filings("2026-01-01", "2026-03-01")

    def test_reversed_window(self):
        with pytest.raises(live_mcp.McpError, match="on or after"):
            live_mcp._tool_search_filings("2026-09-10", "2026-09-01")

    def test_filters_by_stock_code_and_shapes(self, monkeypatch):
        records = [
            {"stockCode": "01461", "title": "A"},
            {"stockCode": "00005", "title": "B"},
        ]
        monkeypatch.setattr(live_mcp.http, "make_session", lambda: object())
        monkeypatch.setattr(live_mcp.api, "fetch_chunk_via_api", lambda *a, **k: (records, 2))
        out = live_mcp._tool_search_filings("2026-09-01", "2026-09-10", "1461", max_results=10)
        assert out["count"] == 1
        assert out["total_reported"] == 2
        assert out["filings"][0]["stockCode"] == "01461"
        assert out["from"] == "2026-09-01" and out["to"] == "2026-09-10"

    def test_max_results_is_clamped(self, monkeypatch):
        seen = {}

        def fake(session, start, end, limit):
            seen["limit"] = limit
            return [], None

        monkeypatch.setattr(live_mcp.http, "make_session", lambda: object())
        monkeypatch.setattr(live_mcp.api, "fetch_chunk_via_api", fake)
        live_mcp._tool_search_filings("2026-09-01", "2026-09-10", "", max_results=10_000)
        assert seen["limit"] == live_mcp.MAX_MAX_RESULTS

    def test_echoes_only_applied_filters(self, monkeypatch):
        _stub_records(monkeypatch, list(_RICH_RECORDS))
        out = live_mcp._tool_search_filings("2026-09-01", "2026-09-10", "1461")
        assert out["filters"] == {"stock_code": "1461"}

    @pytest.mark.parametrize(
        ("kwargs", "expected_codes"),
        [
            ({"title_query": "dividend"}, ["01461"]),
            ({"title_query": "REPORT"}, ["00005"]),
            ({"document_type": "pdf"}, ["01461"]),
            ({"document_type": "HTML"}, ["00005"]),
            ({"category": "annual"}, ["00005"]),
            ({"stock_name": "hsbc"}, ["00005"]),
            ({"category": "notices"}, ["01461"]),
            ({"title_query": "report", "document_type": "html"}, ["00005"]),
            ({"title_query": "dividend", "document_type": "html"}, []),
        ],
    )
    def test_advanced_filters(self, monkeypatch, kwargs, expected_codes):
        _stub_records(monkeypatch, [dict(r) for r in _RICH_RECORDS])
        out = live_mcp._tool_search_filings("2026-09-01", "2026-09-10", **kwargs)
        assert [f["stockCode"] for f in out["filings"]] == expected_codes

    def test_metadata_is_surfaced_in_results(self, monkeypatch):
        _stub_records(monkeypatch, [dict(_RICH_RECORDS[0])])
        out = live_mcp._tool_search_filings("2026-09-01", "2026-09-10")
        filing = out["filings"][0]
        assert filing["fileType"] == "PDF"
        assert filing["category"] == "Announcements and Notices - [Dividend]"


class TestListFilingFacets:
    def test_counts_by_category_type_and_code(self, monkeypatch):
        _stub_records(monkeypatch, [dict(r) for r in _RICH_RECORDS], total=99)
        out = live_mcp._tool_list_filing_facets("2026-09-01", "2026-09-10")
        assert out["count"] == 2
        assert out["total_reported"] == 99
        assert out["from"] == "2026-09-01" and out["to"] == "2026-09-10"
        assert {"value": "PDF", "count": 1} in out["document_types"]
        assert {"value": "HTML", "count": 1} in out["document_types"]
        assert out["distinct_document_types"] == 2
        assert out["distinct_stock_codes"] == 2
        values = {entry["value"] for entry in out["categories"]}
        assert "Financial Statements - [Annual Report]" in values

    def test_narrows_to_stock_code(self, monkeypatch):
        _stub_records(monkeypatch, [dict(r) for r in _RICH_RECORDS])
        out = live_mcp._tool_list_filing_facets("2026-09-01", "2026-09-10", "1461")
        assert out["count"] == 1
        assert out["stock_codes"] == [{"value": "01461", "count": 1}]

    def test_facet_values_are_capped(self, monkeypatch):
        records = [{"stockCode": f"{i:05d}", "fileType": "PDF"} for i in range(120)]
        _stub_records(monkeypatch, records)
        out = live_mcp._tool_list_filing_facets("2026-09-01", "2026-09-10")
        assert len(out["stock_codes"]) == live_mcp.MAX_FACET_VALUES

    def test_window_rules_still_apply(self):
        with pytest.raises(live_mcp.McpError, match="window too wide"):
            live_mcp._tool_list_filing_facets("2026-01-01", "2026-03-01")


class _FakeResponse:
    def __init__(self, content: bytes, content_type: str = "application/pdf"):
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        return None


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, url, timeout=None):
        return self._response


class TestGetFiling:
    def test_rejects_foreign_host(self):
        with pytest.raises(live_mcp.McpError, match="hkexnews.hk"):
            live_mcp._tool_get_filing("https://evil.example.com/a.pdf")

    def test_download_failure_is_a_tool_error(self, monkeypatch):
        def boom(url, timeout=None):
            raise OSError("connection reset")

        monkeypatch.setattr(live_mcp.http, "make_session", lambda: _FakeSession(None))
        monkeypatch.setattr(_FakeSession, "get", boom)
        with pytest.raises(live_mcp.McpError, match="download failed"):
            live_mcp._tool_get_filing("https://www1.hkexnews.hk/a.pdf")

    def test_extract_false_returns_metadata_only(self, monkeypatch):
        response = _FakeResponse(b"x" * 12)
        monkeypatch.setattr(live_mcp.http, "make_session", lambda: _FakeSession(response))
        out = live_mcp._tool_get_filing("https://www1.hkexnews.hk/a.pdf", extract=False)
        assert out["size_bytes"] == 12
        assert "document_text" not in out

    def test_extract_true_truncates_and_caps(self, monkeypatch):
        response = _FakeResponse(b"x" * 12)
        monkeypatch.setattr(live_mcp.http, "make_session", lambda: _FakeSession(response))
        monkeypatch.setattr(
            extractor,
            "extract_content_with_tables",
            lambda raw, url: ("t" * (live_mcp.MAX_TEXT_CHARS + 10), [{"i": i} for i in range(40)]),
        )
        out = live_mcp._tool_get_filing("https://www1.hkexnews.hk/a.pdf", extract=True)
        assert out["text_truncated"] is True
        assert out["text_length"] == live_mcp.MAX_TEXT_CHARS
        assert len(out["tables"]) == live_mcp.MAX_TABLES
        assert out["tables_omitted"] == 40 - live_mcp.MAX_TABLES


@pytest.mark.skipif(not live_mcp._MCP_AVAILABLE, reason="mcp extra not installed")
class TestServerAssembly:
    def test_build_server_registers_four_tools(self):
        server = live_mcp.build_server()
        names = {tool.name for tool in server._tool_manager.list_tools()}
        assert names == {"get_server_info", "search_filings", "list_filing_facets", "get_filing"}

    def test_build_asgi_returns_a_callable_app(self):
        app = live_mcp.build_asgi()
        assert callable(app)


class TestJsonRpcProtocol:
    def test_initialize(self):
        response = live_mcp.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            }
        )
        assert response.status == 200
        result = response.body["result"]
        assert result["protocolVersion"] == "2025-06-18"
        assert result["serverInfo"]["name"] == live_mcp.SERVER_NAME
        assert "tools" in result["capabilities"]
        assert response.headers["Cache-Control"] == "no-store"

    def test_initialize_negotiates_unknown_version(self):
        response = live_mcp.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "x"}}
        )
        assert response.body["result"]["protocolVersion"] == live_mcp.PROTOCOL_VERSION

    def test_initialize_supports_2025_03_26(self):
        response = live_mcp.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        assert response.body["result"]["protocolVersion"] == "2025-03-26"

    def test_capability_probes_return_empty_lists(self):
        for method, key in (
            ("resources/list", "resources"),
            ("resources/templates/list", "resourceTemplates"),
            ("prompts/list", "prompts"),
        ):
            response = live_mcp.handle_jsonrpc({"jsonrpc": "2.0", "id": 9, "method": method})
            assert response.status == 200
            assert response.body["result"] == {key: []}

    def test_initialized_notification_is_202(self):
        response = live_mcp.handle_jsonrpc(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert response.status == 202
        assert response.body is None

    def test_tools_list(self):
        response = live_mcp.handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in response.body["result"]["tools"]]
        assert names == ["get_server_info", "search_filings", "list_filing_facets", "get_filing"]

    def test_ping(self):
        response = live_mcp.handle_jsonrpc({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        assert response.body["result"] == {}

    def test_tools_call_ok(self):
        response = live_mcp.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "get_server_info", "arguments": {}},
            }
        )
        result = response.body["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["result"]["transport"] == "streamable-http"

    def test_tools_call_unknown_tool_is_error(self):
        response = live_mcp.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope"}}
        )
        assert response.body["result"]["isError"] is True

    def test_tools_call_mcp_error_is_error(self):
        response = live_mcp.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "get_filing",
                    "arguments": {"link": "https://evil.example.com/a.pdf"},
                },
            }
        )
        result = response.body["result"]
        assert result["isError"] is True
        assert "hkexnews.hk" in result["content"][0]["text"]

    def test_unknown_method(self):
        response = live_mcp.handle_jsonrpc({"jsonrpc": "2.0", "id": 7, "method": "does/not/exist"})
        assert response.body["error"]["code"] == live_mcp.METHOD_NOT_FOUND

    def test_invalid_request(self):
        response = live_mcp.handle_jsonrpc({"id": 8, "method": "ping"})
        assert response.body["error"]["code"] == live_mcp.INVALID_REQUEST

    def test_notification_with_no_id_returns_202(self):
        response = live_mcp.handle_jsonrpc({"jsonrpc": "2.0", "method": "ping"})
        assert response.status == 202 and response.body is None

    def test_batch(self):
        response = live_mcp.handle_jsonrpc(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ]
        )
        assert response.status == 200
        assert len(response.body) == 1

    def test_batch_of_notifications_only_is_202(self):
        response = live_mcp.handle_jsonrpc(
            [{"jsonrpc": "2.0", "method": "notifications/initialized"}]
        )
        assert response.status == 202

    def test_parse_error_response(self):
        response = live_mcp.parse_error_response()
        assert response.body["error"]["code"] == live_mcp.PARSE_ERROR
