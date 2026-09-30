"""Read-only MCP server exposing the scraped HKEx corpus to LLM clients.

The server runs over **stdio** and exposes a fixed catalog of read-only tools. It
never scrapes the network, never writes to a sink, and never runs schema DDL; reads
are served by the first configured sink whose capabilities include ``reads``
(``DATABASE_TARGET`` order). Configuration is loaded from the current working
directory exactly as the CLI does.

This module is imported lazily behind the ``mcp`` extra: ``import hkex_scraper`` must
never require the SDK. Tool bodies are split into plain ``_tool_*`` helpers so the
read logic can be unit-tested without the SDK installed.

Nothing here may write to stdout: stdout carries the MCP protocol. Never call
``utils.log()`` from a tool path.
"""

from __future__ import annotations

import functools
import json
import sys
from datetime import date, datetime
from typing import Annotated, Any, Callable, Dict, List, Literal, Optional, Tuple

from . import __version__, config, sinks
from .sinks.base import (
    AGGREGATE_GROUPS,
    SEARCH_ORDER_BY,
    FilingQuery,
    redact,
)
from .utils import ticker_to_record_id

try:  # pragma: no cover - exercised via the mcp extra
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.types import ToolAnnotations
    from pydantic import Field

    _MCP_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the environment
    FastMCP = None  # type: ignore[assignment]
    ToolError = None  # type: ignore[assignment]
    ToolAnnotations = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False

    def Field(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]  # pragma: no cover
        """No-op fallback so parameter annotations import without the SDK."""
        return None


SERVER_NAME = "hkex-filing-scraper"

# Hard server-side caps. The model cannot raise these.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
DEFAULT_TEXT_CHARS = 20_000
MAX_TEXT_CHARS = 200_000
MAX_COVERAGE_ROWS = 200
MAX_TICKERS = 1_000
MAX_BATCH = 50
VERIFY_SAMPLE = 5
MAX_VERIFY_SAMPLE = 50
MAX_BUCKETS = 200
DEFAULT_TOP_N = 20

INSTRUCTIONS = (
    "Read-only access to a scraped HKEx regulatory-filings corpus. Reads are served by "
    "the first configured database sink (DATABASE_TARGET order); call get_server_info or "
    "list_sinks if unsure, and describe_schema before filtering. Use search_filings with "
    "filters (ticker, filing_type, document_status, date_from/date_to, ...) to find ids; "
    "search_documents for full-text search over extracted text; get_statistics for counts "
    "by ticker/type; get_filing to read one filing (text is paged via "
    "text_offset/max_text_chars). This server never writes and never scrapes the network."
)


# Single-value parameters surfaced as JSON-schema enums. Keep these in sync with
# ``sinks.base.SEARCH_ORDER_BY`` / ``sinks.base.AGGREGATE_GROUPS`` (asserted in tests).
OrderBy = Literal["filing_date_desc", "filing_date_asc", "title_asc", "filing_id_asc"]
GroupBy = Literal["company_ticker", "filing_type", "filing_category", "document_status", "exchange"]
Section = Literal["all", "filing", "document", "types", "query"]
IncludeKind = Literal["summary", "sinks", "config"]
ReferenceKind = Literal["referenced_by", "owned"]


class McpError(Exception):
    """An expected, user-actionable tool failure (mapped to ``isError``)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _jsonable(value: Any) -> Any:
    """Recursively convert values into JSON-serialisable equivalents."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _clamp(value: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _enabled_sinks() -> List[sinks.Sink]:
    try:
        return sinks.enabled_sinks()
    except Exception as exc:  # noqa: BLE001 - surfaced as an actionable tool error
        raise McpError(f"database configuration error: {redact(str(exc))}") from None


def _read_sink() -> sinks.Sink:
    try:
        sink = sinks.read_sink()
    except Exception as exc:  # noqa: BLE001
        raise McpError(f"database configuration error: {redact(str(exc))}") from None
    if sink is None:
        raise McpError(
            "no configured sink supports reads; set DATABASE_TARGET to at least one "
            "read-capable sink (see get_server_info)"
        )
    return sink


def _read_sink_id() -> Optional[str]:
    try:
        sink = sinks.read_sink()
    except Exception:  # noqa: BLE001 - informational only
        return None
    return sink.id if sink is not None else None


def _fail(error_code: str, sink_id: str) -> "None":
    raise McpError(f"read from sink '{sink_id}' failed: {redact(error_code)[:200]}")


def _page(
    items: List[Any], offset: int, page_size: int, total: Optional[int] = None
) -> Dict[str, Any]:
    returned = len(items)
    has_more = returned >= page_size
    return {
        "items": items,
        "returned_count": returned,
        "total_count": total,
        "has_more": has_more,
        "next_offset": (offset + returned) if has_more else None,
    }


def _split(value: str) -> Tuple[str, ...]:
    """Split a comma-separated filter value into a de-duplicated tuple."""
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _valid_date(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        raise McpError(f"{field} must be an ISO date (YYYY-MM-DD); got '{value}'") from None
    return value


def _filing_query(
    ticker: str = "",
    stock_code: str = "",
    title_query: str = "",
    text_query: str = "",
    filing_type: str = "",
    filing_category: str = "",
    document_status: str = "",
    exchange: str = "",
    referenced_ticker: str = "",
    source: str = "",
    document_type: str = "",
    date_from: str = "",
    date_to: str = "",
    order_by: str = "filing_date_desc",
) -> FilingQuery:
    return FilingQuery(
        tickers=_split(ticker),
        stock_codes=_split(stock_code),
        title_query=(title_query or "").strip(),
        text_query=(text_query or "").strip(),
        filing_types=_split(filing_type),
        filing_categories=_split(filing_category),
        document_status=_split(document_status),
        exchange=(exchange or "").strip(),
        referenced_ticker=(referenced_ticker or "").strip(),
        source=(source or "").strip(),
        document_type=(document_type or "").strip(),
        date_from=_valid_date(date_from, "date_from"),
        date_to=_valid_date(date_to, "date_to"),
        order_by=order_by if order_by in SEARCH_ORDER_BY else "filing_date_desc",
    )


def _chunk_in_range(row: Dict[str, Any], date_from: str, date_to: str) -> bool:
    if not (date_from or date_to):
        return True
    chunk = str(row.get("chunk_from") or "")[:10]
    if not chunk:
        return False
    if date_from and chunk < date_from:
        return False
    if date_to and chunk > date_to:
        return False
    return True


def _window_detail(
    detail: Dict[str, Any],
    include_text: bool,
    text_offset: int,
    max_text_chars: int,
    include_tables: bool,
) -> Dict[str, Any]:
    """Apply the text-window/table policy shared by get_filing and get_filings."""
    max_chars = _clamp(max_text_chars, 0, MAX_TEXT_CHARS)
    offset = max(0, int(text_offset or 0))
    raw_text = detail.get("document_text") or ""
    total = int(detail.get("document_text_len") or len(raw_text))
    window = raw_text[offset : offset + max_chars] if max_chars else ""
    truncated = include_text and (offset + len(window) < total)

    detail["document_text"] = window if include_text else None
    detail["text_offset"] = offset
    detail["text_limit"] = max_chars
    detail["text_total_chars"] = total
    detail["text_truncated"] = truncated
    detail["next_text_offset"] = (offset + len(window)) if truncated else None
    if not include_tables:
        detail["document_tables"] = None
    return detail


# ---------------------------------------------------------------------------
# Tool bodies (plain functions, no SDK dependency)
# ---------------------------------------------------------------------------
def _tool_get_server_info(include: str = "summary") -> Dict[str, Any]:
    summary = {
        "name": SERVER_NAME,
        "version": __version__,
        "read_only": True,
        "transport": "stdio",
        "database_target": config.DATABASE_TARGET,
        "configured_sinks": config.sink_ids(),
        "read_sink": _read_sink_id(),
        "capabilities": ["read-only", "stdio", "fixed-catalog"],
    }
    include = (include or "summary").strip() or "summary"
    if include == "summary":
        return summary
    if include == "sinks":
        return {"server": summary, "sinks": _tool_list_sinks()}
    if include == "config":
        return {"server": summary, "config": _tool_get_config()}
    raise McpError("include must be one of: summary, sinks, config")


def _tool_list_sinks(sink_id: str = "") -> Dict[str, Any]:
    enabled = {sink.id: sink for sink in _enabled_sinks()}
    read_id = _read_sink_id()
    requested = (sink_id or "").strip()
    rows: List[Dict[str, Any]] = []
    for known_id in sinks.known_ids():
        if requested and known_id != requested:
            continue
        spec = sinks.spec(known_id)
        sink = enabled.get(known_id)
        row: Dict[str, Any] = {
            "id": known_id,
            "label": spec.label,
            "license": spec.license,
            "open_source": spec.source_available,
            "extra": spec.extra,
            "configured": sink is not None,
            "is_read_sink": known_id == read_id,
        }
        if sink is not None:
            available = sink.available()
            row["available"] = available
            if not available:
                row["reason"] = redact(sink.unavailable_reason())
            caps = sink.capabilities
            row["capabilities"] = {
                "model": caps.model,
                "reads": caps.reads,
                "edges": caps.edges,
                "json": caps.json,
                "arrays": caps.arrays,
            }
        rows.append(row)
    if requested and not rows:
        raise McpError(f"unknown sink id '{requested}'; valid ids: {', '.join(sinks.known_ids())}")
    return {"sinks": rows, "read_sink": read_id}


_CONFIG_KEYS = (
    "database_target",
    "sink_ids",
    "read_sink",
    "company_table",
    "company_id_pattern",
    "max_download_workers",
)


def _tool_get_config(key: str = "") -> Dict[str, Any]:
    full: Dict[str, Any] = {
        "database_target": config.DATABASE_TARGET,
        "sink_ids": config.sink_ids(),
        "read_sink": _read_sink_id(),
        "company_table": config.COMPANY_TABLE or None,
        "company_id_pattern": config.COMPANY_ID_PATTERN,
        "max_download_workers": config.MAX_DOWNLOAD_WORKERS,
    }
    key = (key or "").strip()
    if not key:
        return full
    if key not in full:
        raise McpError(f"unknown config key '{key}'; valid keys: {', '.join(_CONFIG_KEYS)}")
    return {"key": key, "value": full[key]}


def _tool_describe_schema(section: str = "all") -> Dict[str, Any]:
    full: Dict[str, Any] = {
        "filing": {
            "filing_id": "string (16-char MD5, primary key)",
            "company_ticker": "string, e.g. 0700.HK",
            "stock_code": "string, e.g. 00700",
            "stock_name": "string | null",
            "exchange": "string (HK)",
            "filing_type": "string (see filing_types)",
            "filing_subtype": "string | null",
            "filing_category": "string (see filing_categories)",
            "title": "string | null",
            "filing_date": "ISO date | null",
            "document_url": "string | null",
            "referenced_tickers": "array<string>",
            "source": "string",
            "updated_at": "ISO datetime",
        },
        "document": {
            "document_size": "integer | null",
            "document_type": "string (pdf|html|xlsx|docx|unknown)",
            "document_hash": "MD5 string | null",
            "document_sha256": "SHA-256 string | null",
            "document_text": "string (extracted Markdown text)",
            "document_text_len": "integer",
            "document_tables": "array<object>",
            "document_table_cnt": "integer",
            "document_status": "string (processed|skipped|failed) | null",
            "document_status_reason": "string | null",
        },
        "filing_types": [
            "Annual Report",
            "Annual Results",
            "Interim Report",
            "Interim Results",
            "Quarterly",
            "Dividend",
            "Transaction",
            "Director",
            "Circular",
            "Meeting",
            "Other",
        ],
        "filing_categories": ["LISTED_COMPANY", "DERIVATIVE_ISSUER", "UNKNOWN"],
        "document_statuses": ["processed", "skipped", "failed"],
        "document_types": ["pdf", "html", "xlsx", "docx", "unknown"],
        "graph_edges": ["has_filing", "references_filing"],
        "query": {
            "filters": [
                "ticker",
                "stock_code",
                "title_query",
                "text_query (search_documents only)",
                "filing_type",
                "filing_category",
                "document_status (processed|skipped|failed|unprocessed)",
                "exchange",
                "referenced_ticker",
                "source",
                "document_type",
                "date_from (YYYY-MM-DD)",
                "date_to (YYYY-MM-DD)",
            ],
            "order_by": list(SEARCH_ORDER_BY),
            "aggregate_group_by": list(AGGREGATE_GROUPS),
        },
    }

    section = (section or "all").strip() or "all"
    if section == "all":
        return full
    if section == "filing":
        return {"filing": full["filing"]}
    if section == "document":
        return {"document": full["document"]}
    if section == "types":
        return {
            "filing_types": full["filing_types"],
            "filing_categories": full["filing_categories"],
            "document_statuses": full["document_statuses"],
            "document_types": full["document_types"],
            "graph_edges": full["graph_edges"],
        }
    if section == "query":
        return {"query": full["query"]}
    raise McpError("section must be one of: all, filing, document, types, query")


def _tool_count_filings(
    ticker: str = "",
    filing_type: str = "",
    filing_category: str = "",
    document_status: str = "",
    date_from: str = "",
    date_to: str = "",
) -> Dict[str, Any]:
    configured = _enabled_sinks()
    if not configured:
        raise McpError("no sinks are configured; set DATABASE_TARGET (see get_server_info)")
    filtered = any((ticker, filing_type, filing_category, document_status, date_from, date_to))
    query = (
        _filing_query(
            ticker=ticker,
            filing_type=filing_type,
            filing_category=filing_category,
            document_status=document_status,
            date_from=date_from,
            date_to=date_to,
        )
        if filtered
        else None
    )
    counts: Dict[str, Any] = {}
    for sink in configured:
        if not sink.available():
            counts[sink.id] = {"error": redact(sink.unavailable_reason())}
            continue
        if query is None:
            count, error_code = sink.count_filings()
        else:
            count, error_code = sink.count_matching(query)
        counts[sink.id] = count if not error_code else {"error": redact(error_code)[:200]}
    return {"read_sink": _read_sink_id(), "filtered": filtered, "counts": counts}


def _tool_list_tickers(limit: int, offset: int, ticker: str = "") -> Dict[str, Any]:
    limit = _clamp(limit, 1, MAX_TICKERS)
    offset = max(0, int(offset or 0))
    sink = _read_sink()
    tickers, error_code = sink.distinct_company_tickers()
    if error_code:
        _fail(error_code, sink.id)
    tickers = sorted(t for t in tickers if t)
    query = (ticker or "").strip().lower()
    if query:
        tickers = [t for t in tickers if query in t.lower()]
    window = tickers[offset : offset + limit]
    return {"read_sink": sink.id, **_page(window, offset, limit, total=len(tickers))}


def _tool_search_filings(
    ticker: str = "",
    stock_code: str = "",
    title_query: str = "",
    filing_type: str = "",
    filing_category: str = "",
    document_status: str = "",
    exchange: str = "",
    referenced_ticker: str = "",
    source: str = "",
    document_type: str = "",
    date_from: str = "",
    date_to: str = "",
    order_by: str = "filing_date_desc",
    offset: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    page_size = _clamp(page_size, 1, MAX_PAGE_SIZE)
    offset = max(0, int(offset or 0))
    query = _filing_query(
        ticker=ticker,
        stock_code=stock_code,
        title_query=title_query,
        filing_type=filing_type,
        filing_category=filing_category,
        document_status=document_status,
        exchange=exchange,
        referenced_ticker=referenced_ticker,
        source=source,
        document_type=document_type,
        date_from=date_from,
        date_to=date_to,
        order_by=order_by,
    )
    sink = _read_sink()
    rows, error_code = sink.search_filings(query, offset, page_size)
    if error_code:
        _fail(error_code, sink.id)
    return {"read_sink": sink.id, **_page(rows, offset, page_size)}


def _tool_search_documents(
    text_query: str,
    ticker: str = "",
    filing_type: str = "",
    document_status: str = "",
    source: str = "",
    document_type: str = "",
    date_from: str = "",
    date_to: str = "",
    order_by: str = "filing_date_desc",
    offset: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    if not (text_query or "").strip():
        raise McpError("text_query is required; it is matched against extracted document text")
    page_size = _clamp(page_size, 1, MAX_PAGE_SIZE)
    offset = max(0, int(offset or 0))
    query = _filing_query(
        ticker=ticker,
        text_query=text_query,
        filing_type=filing_type,
        document_status=document_status,
        source=source,
        document_type=document_type,
        date_from=date_from,
        date_to=date_to,
        order_by=order_by,
    )
    sink = _read_sink()
    rows, error_code = sink.search_documents(query, offset, page_size)
    if error_code:
        _fail(error_code, sink.id)
    return {
        "read_sink": sink.id,
        "query": text_query,
        "snippets_supported": bool(sink.capabilities.snippets),
        **_page(rows, offset, page_size),
    }


def _tool_get_statistics(
    group_by: str,
    ticker: str = "",
    title_query: str = "",
    filing_type: str = "",
    filing_category: str = "",
    document_status: str = "",
    exchange: str = "",
    source: str = "",
    document_type: str = "",
    date_from: str = "",
    date_to: str = "",
    top_n: int = DEFAULT_TOP_N,
    min_count: int = 1,
) -> Dict[str, Any]:
    if group_by not in AGGREGATE_GROUPS:
        raise McpError(f"group_by must be one of: {', '.join(AGGREGATE_GROUPS)}")
    top_n = _clamp(top_n, 1, MAX_BUCKETS)
    min_count = max(0, int(min_count or 0))
    query = _filing_query(
        ticker=ticker,
        title_query=title_query,
        filing_type=filing_type,
        filing_category=filing_category,
        document_status=document_status,
        exchange=exchange,
        source=source,
        document_type=document_type,
        date_from=date_from,
        date_to=date_to,
    )
    sink = _read_sink()
    buckets, error_code = sink.aggregate_filings(group_by, query)
    if error_code:
        _fail(error_code, sink.id)
    total = sum(int(bucket.get("count") or 0) for bucket in buckets)
    filtered = [bucket for bucket in buckets if int(bucket.get("count") or 0) >= min_count]
    top = filtered[:top_n]
    return {
        "read_sink": sink.id,
        "group_by": group_by,
        "total": total,
        "total_buckets": len(filtered),
        "returned_count": len(top),
        "has_more": len(filtered) > top_n,
        "buckets": top,
    }


def _tool_list_companies(
    ticker: str = "", limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
) -> Dict[str, Any]:
    limit = _clamp(limit, 1, MAX_PAGE_SIZE)
    offset = max(0, int(offset or 0))
    ticker = (ticker or "").strip()
    sink = _read_sink()
    rows, error_code = sink.list_companies(limit, offset, ticker)
    if error_code:
        _fail(error_code, sink.id)
    return {"read_sink": sink.id, **_page(rows, offset, limit)}


def _tool_list_pending_filings(
    document_status: str = "unprocessed", limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
) -> Dict[str, Any]:
    limit = _clamp(limit, 1, MAX_PAGE_SIZE)
    offset = max(0, int(offset or 0))
    statuses = _split(document_status) or ("unprocessed",)
    query = FilingQuery(document_status=statuses)
    sink = _read_sink()
    rows, error_code = sink.search_filings(query, offset, limit)
    if error_code:
        _fail(error_code, sink.id)
    total: Optional[int] = None
    if statuses == ("unprocessed",):
        count, count_error = sink.count_pending_filings()
        if not count_error:
            total = count
    has_more = (total is not None and total > offset + len(rows)) or (
        total is None and len(rows) >= limit
    )
    return {
        "read_sink": sink.id,
        "document_status": list(statuses),
        "total_count": total,
        "items": rows,
        "returned_count": len(rows),
        "has_more": has_more,
        "next_offset": (offset + len(rows)) if has_more else None,
    }


def _tool_get_filing(
    filing_id: str,
    include_text: bool,
    text_offset: int,
    max_text_chars: int,
    include_tables: bool,
) -> Dict[str, Any]:
    if not filing_id:
        raise McpError("filing_id is required; use search_filings to find one")
    sink = _read_sink()
    detail, error_code = sink.fetch_filing_detail(filing_id)
    if error_code:
        _fail(error_code, sink.id)
    if detail is None:
        raise McpError(
            f"filing_id '{filing_id}' was not found; use search_filings or list_tickers "
            "to discover ids"
        )
    return _window_detail(detail, include_text, text_offset, max_text_chars, include_tables)


def _tool_get_filings(
    filing_ids: List[str],
    include_text: bool = False,
    max_text_chars: int = DEFAULT_TEXT_CHARS,
    include_tables: bool = False,
) -> Dict[str, Any]:
    ids = [str(filing_id).strip() for filing_id in (filing_ids or []) if str(filing_id).strip()]
    ids = ids[:MAX_BATCH]
    if not ids:
        raise McpError("filing_ids is required; provide one or more ids from search_filings")
    sink = _read_sink()
    items: List[Dict[str, Any]] = []
    not_found: List[str] = []
    for filing_id in ids:
        detail, error_code = sink.fetch_filing_detail(filing_id)
        if error_code:
            _fail(error_code, sink.id)
        if detail is None:
            not_found.append(filing_id)
            continue
        items.append(_window_detail(detail, include_text, 0, max_text_chars, include_tables))
    return {
        "read_sink": sink.id,
        "items": items,
        "returned_count": len(items),
        "not_found": not_found,
    }


def _tool_get_coverage(
    date_from: str = "", date_to: str = "", limit: int = MAX_COVERAGE_ROWS
) -> Dict[str, Any]:
    limit = _clamp(limit, 1, MAX_COVERAGE_ROWS)
    date_from = _valid_date(date_from, "date_from")
    date_to = _valid_date(date_to, "date_to")
    sink = _read_sink()
    rows, error_code = sink.fetch_coverage()
    if error_code:
        _fail(error_code, sink.id)
    filtered = [row for row in rows if _chunk_in_range(row, date_from, date_to)]
    window = filtered[:limit]
    api = sum(int(row.get("api_count") or 0) for row in filtered)
    ingested = sum(int(row.get("ingested_count") or 0) for row in filtered)
    unique = sum(int(row.get("unique_count") or 0) for row in filtered)
    return {
        "read_sink": sink.id,
        "items": window,
        "returned_count": len(window),
        "total_count": len(filtered),
        "has_more": len(filtered) > limit,
        "next_offset": limit if len(filtered) > limit else None,
        "totals": {
            "api_count": api,
            "ingested_count": ingested,
            "unique_count": unique,
            "coverage_percent": round(unique / api * 100, 2) if api else None,
        },
    }


def _tool_get_parity(sinks_subset: str = "") -> Dict[str, Any]:
    configured = _enabled_sinks()
    subset = _split(sinks_subset)
    if subset:
        by_id = {sink.id: sink for sink in configured}
        unknown = [name for name in subset if name not in by_id]
        if unknown:
            raise McpError(f"unknown sink ids: {', '.join(unknown)}")
        configured = [by_id[name] for name in subset]
    if len(configured) < 2:
        raise McpError("parity requires two or more configured sinks")
    counts: Dict[str, int] = {}
    for sink in configured:
        if not sink.available():
            raise McpError(f"sink '{sink.id}' is unavailable: {redact(sink.unavailable_reason())}")
        count, error_code = sink.count_filings()
        if error_code:
            _fail(error_code, sink.id)
        counts[sink.id] = count
    spread = max(counts.values()) - min(counts.values())
    return {
        "counts": counts,
        "spread": spread,
        "parity": "OK" if spread == 0 else "MISMATCH",
    }


def _tool_verify_sinks(sample_size: int = VERIFY_SAMPLE, sinks_subset: str = "") -> Dict[str, Any]:
    sample_size = _clamp(sample_size, 1, MAX_VERIFY_SAMPLE)
    configured = _enabled_sinks()
    subset = _split(sinks_subset)
    if subset:
        by_id = {sink.id: sink for sink in configured}
        unknown = [name for name in subset if name not in by_id]
        if unknown:
            raise McpError(f"unknown sink ids: {', '.join(unknown)}")
        configured = [by_id[name] for name in subset]
    if len(configured) < 2:
        raise McpError("verify requires two or more configured sinks")
    digests: Dict[str, Dict[str, str]] = {}
    for sink in configured:
        if not sink.available():
            raise McpError(f"sink '{sink.id}' is unavailable: {redact(sink.unavailable_reason())}")
        rows, error_code = sink.read_filing_digests()
        if error_code:
            if error_code.endswith("UNSUPPORTED"):
                continue
            _fail(error_code, sink.id)
        digests[sink.id] = {row["filing_id"]: row.get("document_sha256") or "" for row in rows}
    if len(digests) < 2:
        raise McpError("fewer than two sinks can enumerate filings; nothing to compare")

    reference_id = next(iter(digests))
    reference = digests[reference_id]
    problems: List[Dict[str, Any]] = []
    for sink_id, current in digests.items():
        if sink_id == reference_id:
            continue
        missing = sorted(set(reference) - set(current))
        extra = sorted(set(current) - set(reference))
        mismatched = sorted(
            filing_id
            for filing_id in set(reference) & set(current)
            if reference[filing_id]
            and current[filing_id]
            and reference[filing_id] != current[filing_id]
        )
        if missing:
            problems.append(
                {
                    "sink": sink_id,
                    "kind": "missing",
                    "count": len(missing),
                    "sample": missing[:sample_size],
                }
            )
        if extra:
            problems.append(
                {
                    "sink": sink_id,
                    "kind": "extra",
                    "count": len(extra),
                    "sample": extra[:sample_size],
                }
            )
        if mismatched:
            problems.append(
                {
                    "sink": sink_id,
                    "kind": "hash_mismatch",
                    "count": len(mismatched),
                    "sample": mismatched[:sample_size],
                }
            )
    return {
        "comparable": len(digests),
        "reference": reference_id,
        "ok": not problems,
        "problems": problems,
    }


_REFERENCE_KINDS = {"owned": "has_filing", "referenced_by": "references_filing"}


def _tool_list_references(ticker: str, kind: str, limit: int, offset: int) -> Dict[str, Any]:
    ticker = (ticker or "").strip()
    if not ticker:
        raise McpError("ticker is required; use list_tickers to discover tickers")
    edge_kind = _REFERENCE_KINDS.get(kind)
    if edge_kind is None:
        raise McpError("kind must be one of: referenced_by, owned")
    limit = _clamp(limit, 1, MAX_PAGE_SIZE)
    offset = max(0, int(offset or 0))
    company_id = ticker_to_record_id(ticker)
    sink = _read_sink()
    rows, error_code = sink.list_edges(edge_kind, company_id, limit, offset)
    if error_code:
        _fail(error_code, sink.id)
    return {
        "read_sink": sink.id,
        "ticker": ticker,
        "company_id": company_id,
        "kind": kind,
        **_page(rows, offset, limit),
    }


# ---------------------------------------------------------------------------
# MCP-facing tools
# ---------------------------------------------------------------------------
def _as_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Convert :class:`McpError` into an MCP ``isError`` result and JSON-normalise."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return _jsonable(fn(*args, **kwargs))
        except McpError as exc:
            raise ToolError(str(exc)) from None

    return wrapper


@_as_tool
def get_server_info(
    include: Annotated[
        IncludeKind,
        Field(
            description="summary (server metadata only), sinks, or config; sinks/config fold in list_sinks output and the configuration payload."
        ),
    ] = "summary",
) -> Dict[str, Any]:
    """Use this first to learn the server version, configured sinks, and read sink.

    Use include="config" for the raw configuration values or list_sinks for per-sink detail.
    ``include`` is one of: summary (server metadata only, default), sinks (also returns the
    list_sinks payload), or config (also returns the configuration payload) — so you can pull
    the summary and the detail in a single call. Returns server metadata only; it reads no
    filings. include="sinks" is an alias for the list_sinks payload — call list_sinks directly
    when sinks are the only thing you need. This tool is read-only.
    """
    return _tool_get_server_info(include)


@_as_tool
def list_sinks(
    sink_id: Annotated[
        str, Field(description="Narrow to a single sink id, e.g. 'postgres'; empty returns all.")
    ] = "",
) -> Dict[str, Any]:
    """Use this when the user asks which databases are configured or their capabilities.

    Use get_server_info (with include="config") for the raw DATABASE_TARGET string or a
    one-line summary instead. Pass ``sink_id`` to inspect one sink without the full list.
    Returns every known sink id with its license, optional extra, configured/available status,
    per-sink capabilities, and which sink serves reads. Reads no filings. get_server_info with
    include="sinks" returns this same payload; call this tool directly when per-sink detail is
    all you need.
    """
    return _tool_list_sinks(sink_id)


@_as_tool
def describe_schema(
    section: Annotated[
        Section,
        Field(
            description="Narrow to one section: filing, document, types, query, or all (default)."
        ),
    ] = "all",
) -> Dict[str, Any]:
    """Use this before filtering or interpreting results to learn the canonical fields.

    Use search_filings or search_documents to query filings once you know the field names.
    Pass ``section`` to return just one section (saves tokens); default returns everything:
    filing and document fields, the known filing types/categories/statuses/types, graph
    edge kinds, and the supported query filters. Reads no filings.
    """
    return _tool_describe_schema(section)


@_as_tool
def count_filings(
    ticker: Annotated[
        str,
        Field(description="Count only this ticker, e.g. 0700.HK; comma-separate to match several."),
    ] = "",
    filing_type: Annotated[
        str,
        Field(
            description="Count only this filing type, e.g. 'Annual Report'; comma-separate for several."
        ),
    ] = "",
    filing_category: Annotated[
        str, Field(description="Count only this filing category, e.g. LISTED_COMPANY.")
    ] = "",
    document_status: Annotated[
        str,
        Field(
            description="Count only this document status: processed, skipped, failed, or unprocessed."
        ),
    ] = "",
    date_from: Annotated[
        str, Field(description="Count only filings on/after this date, YYYY-MM-DD inclusive.")
    ] = "",
    date_to: Annotated[
        str, Field(description="Count only filings on/before this date, YYYY-MM-DD inclusive.")
    ] = "",
) -> Dict[str, Any]:
    """Use this to report how many filings each configured sink holds.

    Choose between: this tool gives per-sink totals across every configured sink; get_statistics
    breaks a single population down by one dimension; get_coverage reports scrape coverage by
    month; verify_sinks(mode="counts") compares counts across sinks and mode="hashes" checks
    per-filing hashes. With
    no filters this returns a per-sink total; with filters it returns the count of matching
    filings per sink (relational sinks only — others report an unsupported error). Counts only;
    it does not return rows.
    """
    return _tool_count_filings(
        ticker, filing_type, filing_category, document_status, date_from, date_to
    )


@_as_tool
def list_tickers(
    ticker: Annotated[
        str, Field(description="Case-insensitive substring match, e.g. '0700'; empty lists all.")
    ] = "",
    limit: Annotated[
        int, Field(description="Maximum tickers to return (1..1000).")
    ] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Field(description="Zero-based offset for paging.")] = 0,
) -> Dict[str, Any]:
    """Use this to list the distinct company tickers that have filings.

    Use list_companies instead to include company names and filing counts. ``ticker`` is a
    case-insensitive substring match (e.g. '0700' matches '0700.HK'); empty lists every
    ticker, alphabetically sorted and paged via ``limit``/``offset``. Use search_filings to
    fetch filings for a ticker. Choose list_companies when you need company names or filing
    counts alongside the code.
    """
    return _tool_list_tickers(limit, offset, ticker)


@_as_tool
def list_companies(
    ticker: Annotated[
        str, Field(description="Case-insensitive substring match, e.g. '0700'; empty lists all.")
    ] = "",
    limit: Annotated[
        int, Field(description="Maximum companies to return (1..100).")
    ] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Field(description="Zero-based offset for paging.")] = 0,
) -> Dict[str, Any]:
    """Use this to list companies (ticker and name) with their filing counts.

    Use list_tickers instead to list just the ticker codes. ``ticker`` is a case-insensitive
    substring match (e.g. '0700' matches '0700.HK'); empty returns every company, ordered by
    filing count descending (ties broken by ticker ascending) and paged via
    ``limit``/``offset``. Use search_filings for a company's filings. This tool is read-only.
    """
    return _tool_list_companies(ticker, limit, offset)


@_as_tool
def search_filings(
    ticker: Annotated[
        str,
        Field(description="Company ticker filter, e.g. 0700.HK; comma-separate to match several."),
    ] = "",
    stock_code: Annotated[
        str,
        Field(
            description="Numeric stock code filter, e.g. 00700; comma-separate to match several."
        ),
    ] = "",
    title_query: Annotated[
        str, Field(description="Case-insensitive substring matched against the filing title.")
    ] = "",
    filing_type: Annotated[
        str,
        Field(description="Filing type(s), e.g. 'Annual Report'; comma-separate to match several."),
    ] = "",
    filing_category: Annotated[
        str,
        Field(
            description="Filing category(ies), e.g. LISTED_COMPANY; comma-separate to match several."
        ),
    ] = "",
    document_status: Annotated[
        str,
        Field(
            description="Document status(es): processed, skipped, failed, or unprocessed; comma-separate to match several."
        ),
    ] = "",
    exchange: Annotated[str, Field(description="Exchange code, e.g. HK.")] = "",
    referenced_ticker: Annotated[
        str, Field(description="Ticker referenced by the filing (graph edge).")
    ] = "",
    source: Annotated[str, Field(description="Source of the filing, e.g. HKEx.")] = "",
    document_type: Annotated[
        str, Field(description="Document type: pdf, html, xlsx, docx, or unknown.")
    ] = "",
    date_from: Annotated[
        str, Field(description="Earliest filing date, YYYY-MM-DD inclusive.")
    ] = "",
    date_to: Annotated[str, Field(description="Latest filing date, YYYY-MM-DD inclusive.")] = "",
    order_by: Annotated[
        OrderBy, Field(description="Sort order of the result.")
    ] = "filing_date_desc",
    offset: Annotated[int, Field(description="Zero-based offset for paging.")] = 0,
    page_size: Annotated[
        int, Field(description="Maximum filings to return (1..100).")
    ] = DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """Use this to find filings by ticker, type, status, date range, or title text.

    Prefer search_documents for full-text search over extracted text, and get_filing or
    get_filings to read filings whose ids you already have. Filters are optional and
    combinable; comma-separate a value to match several (e.g. ``filing_type="Annual
    Report,Dividend"``). ``document_status`` accepts the real statuses plus ``unprocessed``
    (no document yet). ``document_type`` accepts pdf, html, xlsx, docx, or unknown, and
    ``source`` is a free-text origin like HKEx. ``date_from``/``date_to`` are ``YYYY-MM-DD``
    inclusive. ``order_by`` is one of
    filing_date_desc (default), filing_date_asc, title_asc, filing_id_asc. Returns paged
    filing rows (no document text); call get_filing for the document. This tool is read-only.
    """
    return _tool_search_filings(
        ticker,
        stock_code,
        title_query,
        filing_type,
        filing_category,
        document_status,
        exchange,
        referenced_ticker,
        source,
        document_type,
        date_from,
        date_to,
        order_by,
        offset,
        page_size,
    )


@_as_tool
def search_documents(
    text_query: Annotated[
        str, Field(description="Case-insensitive term matched against extracted document text.")
    ],
    ticker: Annotated[
        str,
        Field(description="Company ticker filter, e.g. 0700.HK; comma-separate to match several."),
    ] = "",
    filing_type: Annotated[
        str,
        Field(description="Filing type(s), e.g. 'Annual Report'; comma-separate to match several."),
    ] = "",
    document_status: Annotated[
        str,
        Field(
            description="Document status(es): processed, skipped, failed, or unprocessed; comma-separate to match several."
        ),
    ] = "",
    source: Annotated[str, Field(description="Source of the filing, e.g. HKEx.")] = "",
    document_type: Annotated[
        str, Field(description="Document type: pdf, html, xlsx, docx, or unknown.")
    ] = "",
    date_from: Annotated[
        str, Field(description="Earliest filing date, YYYY-MM-DD inclusive.")
    ] = "",
    date_to: Annotated[str, Field(description="Latest filing date, YYYY-MM-DD inclusive.")] = "",
    order_by: Annotated[
        OrderBy, Field(description="Sort order of the result.")
    ] = "filing_date_desc",
    offset: Annotated[int, Field(description="Zero-based offset for paging.")] = 0,
    page_size: Annotated[
        int, Field(description="Maximum filings to return (1..100).")
    ] = DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """Use this for full-text search over extracted document text.

    Use search_filings instead to filter by metadata without a text query. Matches
    ``text_query`` case-insensitively inside ``document_text`` and returns filing rows with
    a ``snippet`` when the sink supports it (see ``snippets_supported``). ``document_type``
    accepts pdf, html, xlsx, docx, or unknown; ``source`` is a free-text origin like HKEx.
    All filters combine with AND semantics (a filing must match every filter you set).
    Returns nothing until documents are processed. This tool is read-only.
    """
    return _tool_search_documents(
        text_query,
        ticker,
        filing_type,
        document_status,
        source,
        document_type,
        date_from,
        date_to,
        order_by,
        offset,
        page_size,
    )


@_as_tool
def get_statistics(
    group_by: Annotated[
        GroupBy, Field(description="Dimension to count filings by.")
    ] = "company_ticker",
    ticker: Annotated[
        str,
        Field(description="Company ticker filter, e.g. 0700.HK; comma-separate to match several."),
    ] = "",
    title_query: Annotated[
        str, Field(description="Case-insensitive substring matched against the filing title.")
    ] = "",
    filing_type: Annotated[
        str,
        Field(description="Filing type(s), e.g. 'Annual Report'; comma-separate to match several."),
    ] = "",
    filing_category: Annotated[
        str,
        Field(
            description="Filing category(ies), e.g. LISTED_COMPANY; comma-separate to match several."
        ),
    ] = "",
    document_status: Annotated[
        str,
        Field(
            description="Document status(es): processed, skipped, failed, or unprocessed; comma-separate to match several."
        ),
    ] = "",
    exchange: Annotated[str, Field(description="Exchange code, e.g. HK.")] = "",
    source: Annotated[str, Field(description="Source of the filing, e.g. HKEx.")] = "",
    document_type: Annotated[
        str, Field(description="Document type: pdf, html, xlsx, docx, or unknown.")
    ] = "",
    date_from: Annotated[
        str, Field(description="Earliest filing date, YYYY-MM-DD inclusive.")
    ] = "",
    date_to: Annotated[str, Field(description="Latest filing date, YYYY-MM-DD inclusive.")] = "",
    top_n: Annotated[int, Field(description="Maximum buckets to return (1..200).")] = DEFAULT_TOP_N,
    min_count: Annotated[
        int, Field(description="Only return buckets with at least this many filings (0 = all).")
    ] = 1,
) -> Dict[str, Any]:
    """Use this to count filings grouped by one dimension.

    Choose between: count_filings gives a per-sink total across every sink, and get_coverage
    reports scrape coverage by month; use this tool for a grouped breakdown of one population.
    ``group_by`` is one of: company_ticker (default), filing_type, filing_category, document_status,
    exchange. Optional filters narrow the population and combine with AND semantics (a
    filing must match every filter you set). ``top_n`` caps the returned buckets and
    ``min_count`` drops small buckets, but ``total`` still counts every matching filing.
    Buckets are sorted by count descending. This tool is read-only.
    """
    return _tool_get_statistics(
        group_by,
        ticker,
        title_query,
        filing_type,
        filing_category,
        document_status,
        exchange,
        source,
        document_type,
        date_from,
        date_to,
        top_n,
        min_count,
    )


@_as_tool
def get_filing(
    filing_id: Annotated[
        str, Field(description="16-character filing id; obtain from search_filings.")
    ],
    include_text: Annotated[
        bool, Field(description="Include the extracted document text window.")
    ] = True,
    text_offset: Annotated[
        int, Field(description="Character offset into document_text for paging.")
    ] = 0,
    max_text_chars: Annotated[
        int, Field(description="Maximum characters of text to return (0..200000).")
    ] = DEFAULT_TEXT_CHARS,
    include_tables: Annotated[
        bool, Field(description="Include extracted document tables.")
    ] = False,
) -> Dict[str, Any]:
    """Use this to read one filing's metadata and extracted document content.

    Returns the canonical filing and document fields. ``document_text`` is a window of
    ``max_text_chars`` from ``text_offset``; when ``text_truncated`` is true, call again
    with ``next_text_offset`` for more. Tables (``document_tables``) are included only when
    ``include_tables`` is true. Obtain ids from search_filings. This tool is read-only.
    """
    return _tool_get_filing(filing_id, include_text, text_offset, max_text_chars, include_tables)


@_as_tool
def get_filings(
    filing_ids: Annotated[
        List[str], Field(description="Filing ids to fetch (1..50); obtain from search_filings.")
    ],
    include_text: Annotated[
        bool, Field(description="Include the extracted document text window (off by default).")
    ] = False,
    max_text_chars: Annotated[
        int, Field(description="Maximum characters of text to return (0..200000).")
    ] = DEFAULT_TEXT_CHARS,
    include_tables: Annotated[
        bool, Field(description="Include extracted document tables.")
    ] = False,
) -> Dict[str, Any]:
    """Use this to read several filings in one call (up to 50 ids).

    Use get_filing instead to read a single filing. Returns each filing's metadata plus,
    when ``include_text`` is true, a bounded text window. Ids not found are listed in
    ``not_found``. Text is off by default. Parameter relationships: ``filing_ids`` must come
    from search_filings (1..50 ids); ``max_text_chars`` applies per filing and only takes
    effect when ``include_text`` is true; ``include_tables`` is independent of the text window.
    Read-only.
    """
    return _tool_get_filings(filing_ids, include_text, max_text_chars, include_tables)


@_as_tool
def get_coverage(
    date_from: Annotated[str, Field(description="Filter by chunk month, YYYY-MM-DD.")] = "",
    date_to: Annotated[str, Field(description="Filter by chunk month, YYYY-MM-DD.")] = "",
    limit: Annotated[
        int, Field(description="Maximum coverage rows to return (1..200).")
    ] = MAX_COVERAGE_ROWS,
) -> Dict[str, Any]:
    """Use this to report scrape coverage per monthly chunk, with totals.

    Use get_statistics for filing counts grouped by a dimension, or verify_sinks for cross-sink
    comparison. ``date_from``/``date_to`` (``YYYY-MM-DD``) filter by chunk month. ``limit``
    caps how many chunks are returned, but ``totals`` always aggregate the full filtered
    range. Rows are newest-first. Read-only.
    """
    return _tool_get_coverage(date_from, date_to, limit)


@_as_tool
def verify_sinks(
    mode: Annotated[
        str,
        Field(
            description='Comparison depth: "hashes" (default) compares per-filing ids and document hashes; "counts" is a quick per-sink count parity check.'
        ),
    ] = "hashes",
    sample_size: Annotated[
        int, Field(description="Max examples to return per problem bucket (1..50).")
    ] = VERIFY_SAMPLE,
    sinks: Annotated[
        str, Field(description="Restrict to a subset of sink ids, comma-separated; empty = all.")
    ] = "",
) -> Dict[str, Any]:
    """Use this to check that configured sinks agree on their filings.

    ``mode="hashes"`` (default) compares (filing_id, document_sha256) sets across comparable
    sinks and returns a bounded sample of any missing/extra/mismatched ids. ``mode="counts"``
    runs a faster count-only parity check, returning per-sink counts and the spread
    (``parity`` is OK when the spread is zero). For a single-sink total use count_filings, for
    a grouped breakdown use get_statistics, and for monthly scrape coverage use get_coverage.
    Parameter relationships: ``sinks`` must list two or more comparable sink ids from
    list_sinks (empty compares every comparable pair); ``sample_size`` bounds the examples
    returned per problem bucket, not the comparison itself, and is ignored by ``mode="counts"``.
    This tool is read-only.
    """
    if mode == "counts":
        return _tool_get_parity(sinks)
    if mode != "hashes":
        raise McpError(f"mode must be 'hashes' or 'counts', got '{mode}'")
    return _tool_verify_sinks(sample_size, sinks)


@_as_tool
def list_references(
    ticker: Annotated[
        str, Field(description="Company ticker, e.g. 0700.HK; use list_tickers to discover.")
    ],
    kind: Annotated[
        ReferenceKind,
        Field(
            description="referenced_by = filings that mention this company; owned = this company's own filings."
        ),
    ] = "referenced_by",
    limit: Annotated[
        int, Field(description="Maximum edges to return (1..100).")
    ] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Field(description="Zero-based offset for paging.")] = 0,
) -> Dict[str, Any]:
    """Use this to explore the graph edges between companies and filings.

    Use search_filings(ticker=...) for a company's own filings or search_filings(
    referenced_ticker=...) to find mentions via the filing column. This reads the canonical
    edge tables keyed on ``ticker`` (format like 0700.HK; discover valid values via
    list_tickers): ``kind="referenced_by"`` returns filings whose title mentions the company
    (cross-references), ``kind="owned"`` returns the company's own filings. Results are
    paged via ``limit``/``offset`` and stay empty until graph linking has been run. This
    tool is read-only.
    """
    return _tool_list_references(ticker, kind, limit, offset)


TOOLS: List[Callable[..., Any]] = [
    get_server_info,
    list_sinks,
    describe_schema,
    count_filings,
    list_tickers,
    list_companies,
    search_filings,
    search_documents,
    get_statistics,
    get_filing,
    get_filings,
    get_coverage,
    verify_sinks,
    list_references,
]


# ---------------------------------------------------------------------------
# Server assembly
# ---------------------------------------------------------------------------
def build_server() -> "FastMCP":
    """Build the FastMCP server with every read-only tool registered."""
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            'MCP support is not installed; install with: pip install "hkex-filing-scraper[mcp]"'
        )
    annotations = ToolAnnotations(  # type: ignore[misc]
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)  # type: ignore[misc]
    for tool in TOOLS:
        server.add_tool(tool, annotations=annotations)

    @server.resource("hkex://schema")
    def schema_resource() -> str:
        return json.dumps(_jsonable(_tool_describe_schema()))

    @server.resource("hkex://coverage")
    def coverage_resource() -> str:
        try:
            payload = _tool_get_coverage("", "", MAX_COVERAGE_ROWS)
        except McpError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(_jsonable(payload))

    @server.resource("hkex://filing/{filing_id}")
    def filing_resource(filing_id: str) -> str:
        try:
            payload = _tool_get_filing(filing_id, True, 0, DEFAULT_TEXT_CHARS, False)
        except McpError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(_jsonable(payload))

    return server  # type: ignore[return-value]


def main(argv: Optional[List[str]] = None) -> None:
    """Console entry point: run the read-only MCP server over stdio."""
    if not _MCP_AVAILABLE:
        sys.stderr.write(
            "Error: MCP support is not installed. Install with: "
            'pip install "hkex-filing-scraper[mcp]"\n'
        )
        raise SystemExit(1)
    server = build_server()
    try:
        server.run(transport="stdio")
    finally:
        sinks.close_all()


if __name__ == "__main__":  # pragma: no cover
    main()
