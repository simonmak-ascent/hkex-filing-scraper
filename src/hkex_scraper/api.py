"""HKEx JSON API: session management, form POST, record fetching, monthly chunking."""

from __future__ import annotations

import html
import re
from calendar import monthrange
from datetime import datetime
from typing import List, Optional, Tuple

from . import http
from .config import HKEX_API_ENDPOINT, HKEX_BASE_URL, HKEX_SEARCH_PAGE
from .utils import squash_ws

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------
REQUESTS_AVAILABLE = http.REQUESTS_AVAILABLE

try:
    from bs4 import BeautifulSoup  # type: ignore

    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

# ---------------------------------------------------------------------------
# Record parsing
# ---------------------------------------------------------------------------


def _as_text(value) -> str:
    """Coerce an API field to text.

    HKEx has emitted ``null`` and bare numbers for fields documented as strings, so never
    assume a string — a single bad field must not lose the whole record.
    """
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _parse_api_record(record: dict) -> dict:
    """Convert a raw HKEx API JSON record into our internal filing format.

    The API returns records like::

        {
            "FILE_INFO": "53KB",
            "NEWS_ID": "12022263",
            "STOCK_NAME": "ZHONGTAIFUTURES",
            "STOCK_CODE": "01461",
            "TITLE": "Articles of Association",
            "FILE_TYPE": "PDF",
            "DATE_TIME": "11/02/2026 19:10",
            "FILE_LINK": "/listedco/listconews/sehk/2026/0211/2026021100854.pdf",
            "LONG_TEXT": "Announcements and Notices - [Other]",
            "TOTAL_COUNT": "14957"
        }
    """
    date_time = _as_text(record.get("DATE_TIME"))
    date_part = date_time.split(" ")[0] if date_time else ""

    raw_code = _as_text(record.get("STOCK_CODE")).split("<br/>")[0].strip()
    raw_name = _as_text(record.get("STOCK_NAME")).split("<br/>")[0].strip()

    file_link = _as_text(record.get("FILE_LINK"))
    if file_link and file_link.startswith("/"):
        file_link = HKEX_BASE_URL + file_link

    title = _as_text(record.get("TITLE"))
    title = title.replace("&#x3b;", ";").replace("&amp;", "&")

    return {
        "date": date_part,
        "stockCode": raw_code,
        "stockName": squash_ws(raw_name),
        "title": squash_ws(title),
        "link": file_link,
        # Additive metadata used by the live MCP gateway for richer results and
        # faceting; the scraper pipeline reads only the keys above.
        "fileType": _as_text(record.get("FILE_TYPE")).strip(),
        "sizeText": _as_text(record.get("FILE_INFO")).strip(),
        "category": squash_ws(html.unescape(_as_text(record.get("LONG_TEXT")))),
        "newsId": _as_text(record.get("NEWS_ID")).strip(),
    }


# ---------------------------------------------------------------------------
# HTML fallback parser (for initial page)
# ---------------------------------------------------------------------------


def _parse_initial_page_html(html: str) -> Tuple[list, int]:
    """Parse the initial HKEx search page HTML to extract the first ~100 filings."""
    if not _BS4_AVAILABLE:
        return [], 0

    soup = BeautifulSoup(html, "html.parser")

    total_count = 0
    text = soup.get_text()
    m = re.search(r"Total records found:\s*([\d,]+)", text)
    if m:
        total_count = int(m.group(1).replace(",", ""))

    filings: List[dict] = []
    for row in soup.select("tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        date_text = cells[0].get_text(strip=True).replace("Release Time:", "").strip()
        date_part = date_text.split(" ")[0] if date_text else ""
        code_text = cells[1].get_text(strip=True).replace("Stock Code:", "").strip()
        code_match = re.search(r"\d{4,5}", code_text)
        code = code_match.group(0) if code_match else ""
        name = re.sub(r"Stock Short Name:\s*", "", cells[2].get_text(strip=True)).strip()
        link_el = cells[3].find("a")
        link, title = "", ""
        if link_el:
            href = link_el.get("href", "")
            link = (HKEX_BASE_URL + href) if href.startswith("/") else href
            title = re.sub(r"Document:\s*", "", link_el.get_text(strip=True)).strip()
        if code and link:
            filings.append(
                {
                    "date": date_part,
                    "stockCode": code,
                    "stockName": squash_ws(name),
                    "title": squash_ws(title),
                    "link": link,
                }
            )
    return filings, total_count


# ---------------------------------------------------------------------------
# Monthly chunking
# ---------------------------------------------------------------------------


def generate_monthly_chunks(
    date_from: datetime, date_to: datetime
) -> List[Tuple[datetime, datetime]]:
    """Generate ``(chunk_from, chunk_to)`` pairs in 1-month increments (newest first).

    HKEx limits searches to one month when no stock code / document type is specified.
    """
    # The API is date-granular (YYYYMMDD), so normalise before deriving bounds. Without
    # this, a `date_from` later in the day than the month end could produce end < start.
    date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = date_to.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_to < date_from:
        return []

    chunks: List[Tuple[datetime, datetime]] = []
    cursor = datetime(date_to.year, date_to.month, 1)
    while cursor >= datetime(date_from.year, date_from.month, 1):
        chunk_start = max(cursor, date_from)
        _, last_day = monthrange(cursor.year, cursor.month)
        chunk_end = min(datetime(cursor.year, cursor.month, last_day), date_to)
        chunks.append((chunk_start, chunk_end))
        if cursor.month == 1:
            cursor = datetime(cursor.year - 1, 12, 1)
        else:
            cursor = datetime(cursor.year, cursor.month - 1, 1)
    return chunks


# ---------------------------------------------------------------------------
# API fetch (single chunk)
# ---------------------------------------------------------------------------


def fetch_chunk_via_api(
    session,
    date_from_yyyymmdd: str,
    date_to_yyyymmdd: str,
    max_records: int = 0,
) -> Tuple[list, Optional[int]]:
    """Fetch all filings for a date range chunk using the HKEx JSON API.

    Steps:
        1. POST the JSF form with from/to dates to set the session's date range.
        2. Call the JSON API with increasing ``rowRange`` to paginate through all records.
    """
    import json

    # Step 1: POST the form to set the date range on the server session
    http.pace()
    page_resp = session.get(
        HKEX_SEARCH_PAGE,
        params={
            "sortDir": "0",
            "sortByRecordDate": "on",
            "searchType": "0",
            "category": "0",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "-2",
            "documentType": "-1",
            "rowRange": "0",
            "lang": "EN",
        },
        timeout=30,
    )
    page_resp.raise_for_status()

    # Extract ViewState and form action
    if _BS4_AVAILABLE:
        soup = BeautifulSoup(page_resp.text, "html.parser")
        vs_el = soup.find("input", {"name": "javax.faces.ViewState"})
        view_state = vs_el["value"] if vs_el else ""
        form_el = soup.find("form")
        form_action = form_el.get("action", "") if form_el else ""
    else:
        vs_match = re.search(r'javax\.faces\.ViewState.*?value="([^"]+)"', page_resp.text)
        view_state = vs_match.group(1) if vs_match else ""
        fa_match = re.search(r'<form[^>]*action="([^"]+)"', page_resp.text)
        form_action = fa_match.group(1) if fa_match else ""

    # The form action is normally a path. If the page layout changes and it disappears,
    # post back to the search page itself rather than failing with an invalid URL.
    if form_action.startswith("/"):
        submit_url = f"{HKEX_BASE_URL}{form_action}"
    elif form_action:
        submit_url = form_action
    else:
        submit_url = HKEX_SEARCH_PAGE
    session.post(
        submit_url,
        data={
            "j_idt10": "j_idt10",
            "j_idt10:loadMoreRange": "100",
            "javax.faces.ViewState": view_state,
            "from": date_from_yyyymmdd,
            "to": date_to_yyyymmdd,
        },
        timeout=30,
    )

    # Step 2: Fetch records via the JSON API
    all_records: list = []
    API_CHUNK = 5000
    fetched = 0
    api_total = None

    while True:
        if max_records > 0 and len(all_records) >= max_records:
            break
        chunk_size = API_CHUNK
        if max_records > 0:
            chunk_size = min(chunk_size, max_records - len(all_records))
        row_range = fetched + chunk_size

        http.pace()
        api_resp = session.get(
            HKEX_API_ENDPOINT,
            params={
                "sortDir": "0",
                "sortByOptions": "DateTime",
                "category": "0",
                "market": "SEHK",
                "stockId": "-1",
                "documentType": "-1",
                "fromDate": date_from_yyyymmdd,
                "toDate": date_to_yyyymmdd,
                "title": "",
                "searchType": "0",
                "t1code": "-2",
                "t2Gcode": "-2",
                "t2code": "-2",
                "rowRange": str(row_range),
                "lang": "E",
            },
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": HKEX_SEARCH_PAGE,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=120,
        )
        api_resp.raise_for_status()

        data = api_resp.json()
        raw_result = data.get("result", "null")
        if not raw_result or raw_result == "null":
            break

        records = json.loads(raw_result)
        if api_total is None and records:
            api_total = int(_as_text(records[0].get("TOTAL_COUNT", "0")) or "0")
        if max_records > 0:
            remaining = max_records - len(all_records)
            if remaining > 0:
                records = records[:remaining]

        new_records = records[fetched:]
        for rec in new_records:
            all_records.append(_parse_api_record(rec))

        fetched = len(records)
        has_next = data.get("hasNextRow", False)
        if not has_next:
            break
        if api_total and fetched >= api_total:
            break

    return all_records, api_total
