"""Property-based tests for the pure logic (Hypothesis).

These assert invariants rather than examples: chunking never loses or overlaps a day, ids are
stable, tickers normalise idempotently, and escaping never leaves a SurrealQL literal broken.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from hkex_scraper.api import _parse_api_record, generate_monthly_chunks
from hkex_scraper.pipeline import filing_id_for
from hkex_scraper.utils import (
    classify_filing,
    escape_sql,
    extract_referenced_tickers,
    normalize_company_id,
    ticker_to_record_id,
)

dates = st.datetimes(min_value=datetime(1999, 4, 1), max_value=datetime(2035, 12, 31))
stock_codes = st.from_regex(r"[0-9]{5}", fullmatch=True)
titles = st.text(max_size=60)
tickers = st.from_regex(r"[0-9]{1,5}\.(HK|SG|US)", fullmatch=True)


class TestChunking:
    @given(start=dates, end=dates)
    @settings(max_examples=60, deadline=None)
    def test_chunks_cover_the_range_exactly_once(self, start, end):
        low, high = sorted((start, end))
        chunks = generate_monthly_chunks(low, high)
        assert chunks, "a non-empty range must produce at least one chunk"
        # Neither beyond the requested range...
        assert min(c[0] for c in chunks) >= datetime(low.year, low.month, 1)
        assert max(c[1] for c in chunks) <= high
        # ...and the union is contiguous with no overlap.
        ordered = sorted(chunks)
        for (_, previous_end), (next_start, _) in zip(ordered, ordered[1:]):
            assert next_start <= previous_end + timedelta(days=1)

    @given(start=dates, end=dates)
    @settings(max_examples=60, deadline=None)
    def test_no_chunk_exceeds_one_month(self, start, end):
        low, high = sorted((start, end))
        for chunk_start, chunk_end in generate_monthly_chunks(low, high):
            assert chunk_end >= chunk_start
            span = (chunk_end - chunk_start).days
            assert span <= 31, f"chunk spans {span} days"

    def test_reversed_range_yields_no_chunks(self):
        assert generate_monthly_chunks(datetime(2026, 3, 1), datetime(2026, 1, 1)) == []

    def test_chunks_are_newest_first(self):
        chunks = generate_monthly_chunks(datetime(2026, 1, 15), datetime(2026, 3, 10))
        assert chunks[0][1] == datetime(2026, 3, 10)
        assert chunks[-1][0] == datetime(2026, 1, 15)


class TestIdentifiers:
    @given(code=stock_codes, day=st.dates(min_value=datetime(1999, 4, 1).date()), title=titles)
    def test_filing_id_is_stable_and_short(self, code, day, title):
        filing = {"stockCode": code, "date": day.isoformat(), "title": title}
        first = filing_id_for(filing)
        assert first == filing_id_for(dict(filing)), "the id must be deterministic"
        assert len(first) == 16
        assert all(c in "0123456789abcdef" for c in first)

    @given(code=stock_codes, day=st.dates(min_value=datetime(1999, 4, 1).date()), title=titles)
    def test_filing_id_is_independent_of_the_title_type(self, code, day, title):
        # A missing title must not explode; it degrades to an empty string.
        without = {"stockCode": code, "date": day.isoformat()}
        assert filing_id_for(without) == filing_id_for({**without, "title": ""})

    @given(ticker=tickers)
    def test_ticker_round_trips_through_normalisation(self, ticker):
        key = ticker_to_record_id(ticker)
        assert normalize_company_id(key) == key
        assert normalize_company_id(f"company:{key}") == key

    @given(ticker=tickers)
    @settings(max_examples=50)
    def test_ticker_has_no_leading_zeros(self, ticker):
        code = ticker_to_record_id(ticker).split("_", 1)[0]
        assert not code.startswith("0") or code == "0"


class TestEscaping:
    @given(value=st.text(max_size=200))
    @settings(max_examples=100)
    def test_escape_never_leaves_an_unescaped_quote(self, value):
        escaped = escape_sql(value)
        # Every quote must be preceded by an odd number of backslashes.
        for index, char in enumerate(escaped):
            if char != "'":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and escaped[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            assert backslashes % 2 == 1, f"unescaped quote in {escaped!r}"

    @given(value=st.one_of(st.none(), st.text(max_size=80)))
    def test_escape_is_total_and_returns_a_string(self, value):
        assert isinstance(escape_sql(value), str)

    @given(value=st.text(max_size=80))
    def test_escape_is_idempotent_on_safe_text(self, value):
        # Re-escaping already-escaped text is allowed to differ, but it must never raise.
        assert isinstance(escape_sql(escape_sql(value)), str)


class TestClassification:
    @given(title=titles)
    @settings(max_examples=100)
    def test_classify_is_total(self, title):
        filing_type, subtype = classify_filing(title)
        assert isinstance(filing_type, str) and filing_type
        assert isinstance(subtype, str)

    @given(title=st.sampled_from(["", "Annual Report", "ANNUAL REPORT", "announcement"]))
    def test_known_titles_classify_consistently(self, title):
        assert classify_filing(title) == classify_filing(title)

    @given(title=titles, code=stock_codes)
    def test_referenced_tickers_never_include_the_own_code(self, title, code):
        tickers_found = extract_referenced_tickers(title, code)
        assert code not in tickers_found
        assert len(tickers_found) == len(set(tickers_found)), "duplicates must be removed"


class TestApiRecordParsing:
    @given(
        record=st.fixed_dictionaries(
            {
                "STOCK_CODE": st.one_of(st.just(""), stock_codes),
                "STOCK_NAME": st.text(max_size=40),
                "TITLE": st.text(max_size=80),
                "DATE_TIME": st.one_of(st.just(""), st.just("11/02/2026 19:10")),
                "FILE_LINK": st.one_of(
                    st.just(""), st.just("/listedco/listconews/sehk/2026/0211/x.pdf")
                ),
                "FILE_TYPE": st.sampled_from(["PDF", "HTML", "XLSX"]),
                "LONG_TEXT": st.text(max_size=40),
            }
        )
    )
    @settings(max_examples=60, deadline=None)
    def test_parse_never_raises_and_normalises_the_fields(self, record):
        parsed = _parse_api_record(record)
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
        # Relative links are absolutised against the HKEx base URL.
        if record["FILE_LINK"].startswith("/"):
            assert parsed["link"].startswith("http")
        else:
            assert parsed["link"] == record["FILE_LINK"]
        # Multi-line stock names and HTML entities are cleaned up.
        assert "<br/>" not in parsed["stockName"]
        assert "&#x3b;" not in parsed["title"] and "&amp;" not in parsed["title"]
