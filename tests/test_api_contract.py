"""Contract tests for the HKEx API using replayed payloads.

`responses` serves the recorded response shapes (the JSF search page, the form POST, and the
JSON endpoint), so the whole `fetch_chunk_via_api` path — ViewState extraction, session POST,
pagination, parsing — runs offline. This is the regression guard for risk R1: if the API shape
changes, these fail before the scraper silently returns nothing.
"""

from __future__ import annotations

import json

import pytest
import responses
from responses.registries import OrderedRegistry

from hkex_scraper import api, config

SEARCH_PAGE_HTML = """
<html><body>
  <form id="searchForm" action="/search/titlesearch.xhtml">
    <input type="hidden" name="javax.faces.ViewState" value="VIEWSTATE-TOKEN-123" />
  </form>
  <span>Total records found: 2</span>
</body></html>
"""


def _api_page(records, total="2"):
    """One JSON API page, in the wire shape the endpoint actually returns."""
    payload = [
        {
            "STOCK_CODE": r["code"],
            "STOCK_NAME": r["name"],
            "TITLE": r["title"],
            "DATE_TIME": r["date"],
            "FILE_TYPE": "PDF",
            "FILE_LINK": r["link"],
            "LONG_TEXT": "Announcements and Notices",
            "TOTAL_COUNT": total,
        }
        for r in records
    ]
    return {"result": json.dumps(payload)}


PAGE_ONE = _api_page(
    [
        {
            "code": "01461",
            "name": "ZHONGTAI FUTURES",
            "title": "Announcement on Continuing Connected Transactions",
            "date": "11/02/2026 19:10",
            "link": "/listedco/listconews/sehk/2026/0211/a.pdf",
        },
        {
            "code": "00005",
            "name": "HSBC HOLDINGS",
            "title": "Announcement on the Completion of an Issue of Securities",
            "date": "11/02/2026 18:02",
            "link": "/listedco/listconews/sehk/2026/0211/b.pdf",
        },
    ]
)


def _session():
    from hkex_scraper import http

    http.reset_session()
    return http.get_session()


class TestFetchChunkContract:
    @responses.activate(registry=OrderedRegistry)
    def test_full_cycle_reads_records_and_stops_at_the_end(self):
        responses.get(config.HKEX_SEARCH_PAGE, body=SEARCH_PAGE_HTML, status=200)
        responses.post(config.HKEX_BASE_URL + "/search/titlesearch.xhtml", body="ok", status=200)
        responses.get(config.HKEX_API_ENDPOINT, json=PAGE_ONE, status=200)
        responses.get(config.HKEX_API_ENDPOINT, json={"result": "null"}, status=200)

        records, total = api.fetch_chunk_via_api(_session(), "20260211", "20260211")

        assert total == 2
        assert [r["stockCode"] for r in records] == ["01461", "00005"]
        assert (
            records[0]["link"] == f"{config.HKEX_BASE_URL}/listedco/listconews/sehk/2026/0211/a.pdf"
        )
        # TOTAL_COUNT says 2 and the page returned both, so pagination stops immediately.
        api_calls = [
            c for c in responses.calls if c.request.url.startswith(config.HKEX_API_ENDPOINT)
        ]
        assert len(api_calls) == 1

    @responses.activate(registry=OrderedRegistry)
    def test_view_state_and_dates_are_posted_back(self):
        responses.get(config.HKEX_SEARCH_PAGE, body=SEARCH_PAGE_HTML, status=200)
        responses.post(config.HKEX_BASE_URL + "/search/titlesearch.xhtml", body="ok", status=200)
        responses.get(config.HKEX_API_ENDPOINT, json={"result": "null"}, status=200)

        api.fetch_chunk_via_api(_session(), "20260101", "20260131")

        posted = next(c for c in responses.calls if c.request.method == "POST")
        body = posted.request.body
        assert "VIEWSTATE-TOKEN-123" in body
        assert "from=20260101" in body and "to=20260131" in body

    @responses.activate(registry=OrderedRegistry)
    def test_max_records_caps_a_page_that_returns_more_rows(self):
        responses.get(config.HKEX_SEARCH_PAGE, body=SEARCH_PAGE_HTML, status=200)
        responses.post(config.HKEX_BASE_URL + "/search/titlesearch.xhtml", body="ok", status=200)
        responses.get(config.HKEX_API_ENDPOINT, json=PAGE_ONE, status=200)

        records, _ = api.fetch_chunk_via_api(_session(), "20260211", "20260211", max_records=1)

        assert len(records) == 1, "the limit must be honoured"
        api_calls = [
            c for c in responses.calls if c.request.url.startswith(config.HKEX_API_ENDPOINT)
        ]
        assert len(api_calls) == 1

    @responses.activate(registry=OrderedRegistry)
    def test_html_numbered_results_use_row_range(self):
        responses.get(config.HKEX_SEARCH_PAGE, body=SEARCH_PAGE_HTML, status=200)
        responses.post(config.HKEX_BASE_URL + "/search/titlesearch.xhtml", body="ok", status=200)
        responses.get(config.HKEX_API_ENDPOINT, json=PAGE_ONE, status=200)
        responses.get(config.HKEX_API_ENDPOINT, json={"result": "null"}, status=200)

        api.fetch_chunk_via_api(_session(), "20260211", "20260211")

        ranges = [
            c.request.url.split("rowRange=")[1].split("&")[0]
            for c in responses.calls
            if c.request.url.startswith(config.HKEX_API_ENDPOINT)
        ]
        assert ranges == ["5000"], f"unexpected rowRange sequence: {ranges}"


class TestTolerantParsing:
    """The parser must survive the shapes HKEx has actually emitted over the years."""

    def test_documented_shape(self):
        parsed = api._parse_api_record(
            {
                "STOCK_CODE": "01461",
                "STOCK_NAME": "ZHONGTAIFUTURES",
                "TITLE": "Articles of Association",
                "DATE_TIME": "11/02/2026 19:10",
                "FILE_TYPE": "PDF",
                "FILE_LINK": "/listedco/listconews/sehk/2026/0211/2026021100854.pdf",
                "LONG_TEXT": "Announcements and Notices - [Other]",
                "TOTAL_COUNT": "14957",
            }
        )
        assert parsed["stockCode"] == "01461"
        assert parsed["date"] == "11/02/2026"
        assert parsed["link"].endswith("2026021100854.pdf")

    @pytest.mark.parametrize(
        "record",
        [
            {},
            {"STOCK_CODE": None, "STOCK_NAME": None, "TITLE": None},
            {"STOCK_CODE": 461, "TITLE": 123, "FILE_LINK": None},
            {"STOCK_CODE": "00005", "UNEXPECTED": "key"},
            {"TITLE": "中文公告 — 持續關連交易"},
            {"TITLE": "x" * 5000},
            {"DATE_TIME": "not-a-date"},
            {"FILE_LINK": "https://example.test/absolute.pdf"},
        ],
    )
    def test_never_raises(self, record):
        parsed = api._parse_api_record(record)
        assert set(parsed) == {
            "date",
            "stockCode",
            "stockName",
            "title",
            "link",
            "fileType",
            "sizeText",
            "category",
            "newsId",
        }
        assert all(isinstance(v, str) for v in parsed.values())

    def test_cjk_title_is_preserved(self):
        parsed = api._parse_api_record({"TITLE": "中文公告"})
        assert parsed["title"] == "中文公告"

    def test_api_payload_with_two_stock_lines_keeps_the_first(self):
        parsed = api._parse_api_record(
            {"STOCK_NAME": "FIRST ISSUER<br/>SECOND ISSUER", "STOCK_CODE": "00005<br/>00006"}
        )
        assert parsed["stockName"] == "FIRST ISSUER"
        assert parsed["stockCode"] == "00005"


class TestMalformedPayloads:
    @responses.activate(registry=OrderedRegistry)
    def test_null_result_terminates_without_records(self):
        responses.get(config.HKEX_SEARCH_PAGE, body=SEARCH_PAGE_HTML, status=200)
        responses.post(config.HKEX_BASE_URL + "/search/titlesearch.xhtml", body="ok", status=200)
        responses.get(config.HKEX_API_ENDPOINT, json={"result": "null"}, status=200)

        records, total = api.fetch_chunk_via_api(_session(), "20260211", "20260211")

        assert records == []
        assert total is None

    @responses.activate(registry=OrderedRegistry)
    def test_missing_result_key_terminates_without_records(self):
        responses.get(config.HKEX_SEARCH_PAGE, body=SEARCH_PAGE_HTML, status=200)
        responses.post(config.HKEX_BASE_URL + "/search/titlesearch.xhtml", body="ok", status=200)
        responses.get(config.HKEX_API_ENDPOINT, json={}, status=200)

        records, _ = api.fetch_chunk_via_api(_session(), "20260211", "20260211")
        assert records == []

    @responses.activate(registry=OrderedRegistry)
    def test_empty_record_list_terminates_without_records(self):
        responses.get(config.HKEX_SEARCH_PAGE, body=SEARCH_PAGE_HTML, status=200)
        responses.post(config.HKEX_BASE_URL + "/search/titlesearch.xhtml", body="ok", status=200)
        responses.get(config.HKEX_API_ENDPOINT, json={"result": "[]"}, status=200)

        records, _ = api.fetch_chunk_via_api(_session(), "20260211", "20260211")
        assert records == []

    @responses.activate(registry=OrderedRegistry)
    def test_page_without_a_view_state_still_attempts_the_request(self):
        responses.get(config.HKEX_SEARCH_PAGE, body="<html><body>no form</body></html>", status=200)
        responses.post(config.HKEX_BASE_URL + "/search/titlesearch.xhtml", body="ok", status=200)
        responses.get(config.HKEX_API_ENDPOINT, json={"result": "null"}, status=200)

        records, _ = api.fetch_chunk_via_api(_session(), "20260211", "20260211")
        assert records == []
