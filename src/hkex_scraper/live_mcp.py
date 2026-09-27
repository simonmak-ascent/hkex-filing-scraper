"""Live HKEx MCP gateway for serverless deployment (Streamable HTTP, stateless).

Unlike :mod:`hkex_scraper.mcp_server` — a **stdio** server that reads a *stored* corpus
from a database sink — this module serves **live** results by calling the HKEx JSON API on
every request. It holds no state, writes nothing, and needs no database, which is what makes
it safe to run as a public serverless function.

The tool logic is transport-agnostic: the ``_tool_*`` helpers and :func:`handle_tool` can be
tested offline and driven by either the MCP SDK's ASGI app (:func:`build_asgi`) or a
hand-rolled JSON-RPC shim. The ``mcp`` SDK is imported lazily behind the ``mcp`` extra, so
``import hkex_scraper`` never requires it.

Security posture (this endpoint is intentionally public and unauthenticated):

* **Read-only by construction** — four read tools, no arbitrary URL/SQL surface.
* **SSRF allowlist** — ``get_filing`` fetches only HKEx document hosts.
* **Bounded** — a hard result cap, a bounded date window, and text/table truncation so a
  response can never approach the platform body limit.
* **Stateless** — a fresh HTTP session per request; no module-global mutable state.

Nothing here may write to stdout when run over stdio: stdout carries the MCP protocol.
"""

from __future__ import annotations

import functools
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse

from . import __version__, api, http
from .config import HKEX_BASE_URL

try:  # pragma: no cover - exercised via the mcp extra
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.types import ToolAnnotations

    _MCP_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the environment
    FastMCP = None  # type: ignore[assignment]
    ToolError = None  # type: ignore[assignment]
    ToolAnnotations = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False


SERVER_NAME = "hkex-filing-scraper-live"
TRANSPORT = "streamable-http"
STREAMABLE_HTTP_PATH = "/mcp"

# Legacy MCP protocol versions whose Streamable HTTP semantics this transport implements.
PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

# Hard server-side caps. The model can raise none of these.
DEFAULT_MAX_RESULTS = 50
MAX_MAX_RESULTS = 200
MAX_WINDOW_DAYS = 31
MAX_TEXT_CHARS = 300_000
MAX_TABLES = 30
MAX_FACET_VALUES = 50
FETCH_TIMEOUT_SECONDS = 60

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")

# Document downloads are restricted to these hosts (and their subdomains).
ALLOWED_DOC_HOSTS = ("hkexnews.hk",)


def _allowed_doc_hosts() -> Tuple[str, ...]:
    hosts: set[str] = set(ALLOWED_DOC_HOSTS)
    host = (urlparse(HKEX_BASE_URL).hostname or "").lower()
    if host:
        hosts.add(host)
    return tuple(sorted(hosts))


INSTRUCTIONS = (
    "Live, read-only access to HKEx (Hong Kong Stock Exchange) regulatory filings. Every "
    "call fetches fresh data from the HKEx website; nothing is stored. Use search_filings "
    "with a date window of at most 31 days to find filings, narrowing with the optional "
    "stock_code, title_query, document_type, category, and stock_name filters; use "
    "list_filing_facets to browse the categories, document types, and stock codes present "
    "in a window before drilling in. Then call get_filing on a returned link to download "
    "and extract its text and tables. This server never writes and never fetches arbitrary "
    "URLs."
)


class McpError(Exception):
    """An expected, user-actionable tool failure (mapped to ``isError``)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _clamp(value: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _parse_date(value: str, field: str):
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise McpError(f"{field} must be YYYY-MM-DD or DD/MM/YYYY; got '{text}'") from None


def _normalise_code(value: Any) -> str:
    return str(value or "").strip().lstrip("0").upper()


def is_allowed_document_url(url: str) -> bool:
    """True only for http(s) URLs on an allowlisted HKEx document host.

    This is the SSRF guard: ``get_filing`` must never fetch an arbitrary URL.
    """
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in _allowed_doc_hosts())


def _truncate_text(text: str, limit: int) -> Tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _cap_tables(tables: List[Any], limit: int) -> Tuple[List[Any], int]:
    if len(tables) <= limit:
        return tables, 0
    return tables[:limit], len(tables) - limit


# ---------------------------------------------------------------------------
# Tool bodies (transport-agnostic)
# ---------------------------------------------------------------------------
def _extraction_dependencies() -> Dict[str, bool]:
    """Report which optional PDF/Excel extractors are importable in this environment."""
    try:
        from . import extractor

        return {
            "pymupdf": bool(getattr(extractor, "PYMUPDF_AVAILABLE", False)),
            "pymupdf4llm": bool(getattr(extractor, "PYMUPDF4LLM_AVAILABLE", False)),
            "camelot": bool(getattr(extractor, "CAMELOT_AVAILABLE", False)),
        }
    except Exception:  # noqa: BLE001 - diagnostic only
        return {"pymupdf": False, "pymupdf4llm": False, "camelot": False}


def _tool_get_server_info() -> Dict[str, Any]:
    return {
        "name": SERVER_NAME,
        "version": __version__,
        "transport": TRANSPORT,
        "live": True,
        "storage": "none",
        "read_only": True,
        "extraction": _extraction_dependencies(),
        "tools": ["get_server_info", "search_filings", "list_filing_facets", "get_filing"],
        "filters": ["stock_code", "title_query", "document_type", "category", "stock_name"],
        "limits": {
            "max_results": MAX_MAX_RESULTS,
            "max_window_days": MAX_WINDOW_DAYS,
            "max_text_chars": MAX_TEXT_CHARS,
            "max_tables": MAX_TABLES,
            "max_facet_values": MAX_FACET_VALUES,
        },
    }


def _fetch_window(
    from_date: str, to_date: str, max_results: int
) -> Tuple[Any, Any, List[Dict[str, Any]], Optional[int]]:
    """Validate a date window and fetch the parsed HKEx records for it.

    Returns ``(start, end, records, total_reported)``. The window is at most
    ``MAX_WINDOW_DAYS`` days and ``max_results`` is clamped to ``MAX_MAX_RESULTS``.
    """
    start = _parse_date(from_date, "from_date")
    end = _parse_date(to_date, "to_date")
    if end < start:
        raise McpError("to_date must be on or after from_date")
    if (end - start).days > MAX_WINDOW_DAYS:
        raise McpError(f"date window too wide: request at most {MAX_WINDOW_DAYS} days per call")

    limit = _clamp(max_results, 1, MAX_MAX_RESULTS)
    session = http.make_session()
    try:
        records, total = api.fetch_chunk_via_api(
            session, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), limit
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as an actionable tool error
        raise McpError(f"HKEx request failed: {type(exc).__name__}") from None
    return start, end, records, total


def _filter_records(
    records: List[Dict[str, Any]],
    stock_code: str = "",
    title_query: str = "",
    document_type: str = "",
    category: str = "",
    stock_name: str = "",
) -> List[Dict[str, Any]]:
    """Apply the optional substring/equality filters to fetched records.

    ``stock_code`` and ``document_type`` match exactly (codes are normalised, types
    compared case-insensitively); ``title_query``, ``category`` and ``stock_name`` are
    case-insensitive substrings. All filters combine with AND semantics.
    """
    filtered = records
    if stock_code:
        needle = _normalise_code(stock_code)
        filtered = [r for r in filtered if _normalise_code(r.get("stockCode")) == needle]
    if title_query:
        needle = title_query.strip().lower()
        filtered = [r for r in filtered if needle in str(r.get("title") or "").lower()]
    if document_type:
        needle = document_type.strip().lower()
        filtered = [r for r in filtered if str(r.get("fileType") or "").lower() == needle]
    if category:
        needle = category.strip().lower()
        filtered = [r for r in filtered if needle in str(r.get("category") or "").lower()]
    if stock_name:
        needle = stock_name.strip().lower()
        filtered = [r for r in filtered if needle in str(r.get("stockName") or "").lower()]
    return filtered


def _tool_search_filings(
    from_date: str,
    to_date: str,
    stock_code: str = "",
    title_query: str = "",
    document_type: str = "",
    category: str = "",
    stock_name: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> Dict[str, Any]:
    start, end, records, total = _fetch_window(from_date, to_date, max_results)
    records = _filter_records(
        records,
        stock_code=stock_code,
        title_query=title_query,
        document_type=document_type,
        category=category,
        stock_name=stock_name,
    )
    applied = {
        "stock_code": (stock_code or "").strip(),
        "title_query": (title_query or "").strip(),
        "document_type": (document_type or "").strip(),
        "category": (category or "").strip(),
        "stock_name": (stock_name or "").strip(),
    }
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(records),
        "total_reported": total,
        "filters": {name: value for name, value in applied.items() if value},
        "filings": records,
    }


def _facet_counts(records: List[Dict[str, Any]], key: str, limit: int) -> List[Dict[str, Any]]:
    """Count non-empty values of ``key`` across ``records``, sorted by frequency."""
    counts: Dict[str, int] = {}
    for record in records:
        value = str(record.get(key) or "").strip()
        if value:
            counts[value] = counts.get(value, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"value": value, "count": count} for value, count in ordered[:limit]]


def _tool_list_filing_facets(
    from_date: str,
    to_date: str,
    stock_code: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> Dict[str, Any]:
    start, end, records, total = _fetch_window(from_date, to_date, max_results)
    records = _filter_records(records, stock_code=stock_code)
    categories = _facet_counts(records, "category", MAX_FACET_VALUES)
    document_types = _facet_counts(records, "fileType", MAX_FACET_VALUES)
    stock_codes = _facet_counts(records, "stockCode", MAX_FACET_VALUES)

    def _distinct(key: str) -> int:
        return len({str(r.get(key) or "").strip() for r in records} - {""})

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(records),
        "total_reported": total,
        "stock_code_filter": (stock_code or "").strip(),
        "distinct_categories": _distinct("category"),
        "distinct_document_types": _distinct("fileType"),
        "distinct_stock_codes": _distinct("stockCode"),
        "categories": categories,
        "document_types": document_types,
        "stock_codes": stock_codes,
    }


def _tool_get_filing(link: str, extract: bool = True) -> Dict[str, Any]:
    if not is_allowed_document_url(link):
        raise McpError("link must be an HKEx document URL under www1.hkexnews.hk")

    session = http.make_session()
    try:
        response = session.get(link, timeout=FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise McpError(f"document download failed: {type(exc).__name__}") from None

    raw = response.content
    result: Dict[str, Any] = {
        "link": link,
        "size_bytes": len(raw),
        "content_type": response.headers.get("Content-Type", ""),
    }
    if not extract:
        return result

    from .extractor import extract_content_with_tables

    text, tables = extract_content_with_tables(raw, link)
    text, truncated = _truncate_text(text, MAX_TEXT_CHARS)
    tables, omitted = _cap_tables(list(tables), MAX_TABLES)
    result.update(
        {
            "document_text": text,
            "text_length": len(text),
            "text_truncated": truncated,
            "tables": tables,
            "tables_omitted": omitted,
        }
    )
    return result


_HANDLERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "get_server_info": _tool_get_server_info,
    "search_filings": _tool_search_filings,
    "list_filing_facets": _tool_list_filing_facets,
    "get_filing": _tool_get_filing,
}


def handle_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dispatch a tool call by name. Transport-agnostic entry point."""
    handler = _HANDLERS.get(str(name))
    if handler is None:
        raise McpError(f"unknown tool: {name}")
    return handler(**(arguments or {}))


# ---------------------------------------------------------------------------
# MCP-facing tools (docstrings become tool descriptions)
# ---------------------------------------------------------------------------
def _as_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except McpError as exc:
            raise ToolError(str(exc)) from None  # type: ignore[misc]

    return wrapper


@_as_tool
def get_server_info() -> Dict[str, Any]:
    """Report this live gateway's version, transport, and hard limits. Reads nothing."""
    return _tool_get_server_info()


@_as_tool
def search_filings(
    from_date: str,
    to_date: str,
    stock_code: str = "",
    title_query: str = "",
    document_type: str = "",
    category: str = "",
    stock_name: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> Dict[str, Any]:
    """Search live HKEx filings in a date window (at most 31 days).

    ``from_date``/``to_date`` accept YYYY-MM-DD or DD/MM/YYYY. All filters are optional and
    combine with AND semantics: ``stock_code`` matches exactly (e.g. ``01461`` or ``1461``);
    ``title_query``, ``category`` and ``stock_name`` are case-insensitive substrings (e.g.
    category ``Dividend``); ``document_type`` matches the file type exactly (e.g. ``PDF`` or
    ``HTML``). Filters are applied to the fetched window, so widen ``max_results`` to catch
    rarer matches. ``max_results`` caps the number of filings fetched and returned (hard cap
    200). Returns the matching filings — each carrying ``fileType``, ``sizeText``,
    ``category`` and ``newsId`` metadata — plus the HKEx-reported total for the window. Use
    list_filing_facets first to see which categories, types and codes exist in a window.
    """
    return _tool_search_filings(
        from_date,
        to_date,
        stock_code,
        title_query,
        document_type,
        category,
        stock_name,
        max_results,
    )


@_as_tool
def list_filing_facets(
    from_date: str,
    to_date: str,
    stock_code: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> Dict[str, Any]:
    """Browse what filings exist in a date window (at most 31 days) without downloading.

    ``from_date``/``to_date`` accept YYYY-MM-DD or DD/MM/YYYY. Returns counts of the
    categories (headline categories), document types, and stock codes present in the window,
    each sorted by frequency and capped at 50 values, plus the distinct counts. Optionally
    narrow to one ``stock_code``. Use this to discover valid filter values before calling
    search_filings; it fetches the window once and extracts no document text.
    """
    return _tool_list_filing_facets(from_date, to_date, stock_code, max_results)


@_as_tool
def get_filing(link: str, extract: bool = True) -> Dict[str, Any]:
    """Download one HKEx document by its URL and extract its text and tables.

    ``link`` must be an HKEx document URL (host ``www1.hkexnews.hk``); any other host is
    rejected. With ``extract=True`` the response includes extracted ``document_text``
    (truncated) and up to 30 ``tables``; set ``extract=False`` for size/content-type only.
    """
    return _tool_get_filing(link, extract)


TOOLS: List[Callable[..., Any]] = [
    get_server_info,
    search_filings,
    list_filing_facets,
    get_filing,
]


# Read-only, idempotent, network-backed: the annotations every live tool carries.
_TOOL_ANNOTATIONS: Dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

# One facet entry: a distinct metadata value and how many filings carry it.
_FACET_ENTRY: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "value": {"type": "string", "description": "The category, file type, or stock code"},
        "count": {"type": "integer", "description": "Filings carrying this value"},
    },
    "required": ["value", "count"],
}

# One returned filing, with the metadata the gateway surfaces.
_FILING_ENTRY: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "Release date, DD/MM/YYYY"},
        "stockCode": {"type": "string"},
        "stockName": {"type": "string"},
        "title": {"type": "string"},
        "link": {"type": "string", "description": "HKEx document URL"},
        "fileType": {"type": "string", "description": "PDF, HTML, XLSX, ..."},
        "sizeText": {"type": "string", "description": "HKEx-reported size, e.g. 53KB"},
        "category": {"type": "string", "description": "HKEx headline category"},
        "newsId": {"type": "string", "description": "HKEx news id"},
    },
    "required": ["stockCode", "title", "link"],
}


def _structured_output(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a tool's return object in the ``{"result": ...}`` structured-content shape."""
    return {
        "type": "object",
        "properties": {"result": fields},
        "required": ["result"],
    }


# Explicit JSON Schemas for the four tools, used by the transport-agnostic shim and tests.
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "get_server_info",
        "description": get_server_info.__doc__ or "",
        "annotations": dict(_TOOL_ANNOTATIONS),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "outputSchema": _structured_output(
            {
                "type": "object",
                "description": "Gateway version, transport, tool list, filters, and hard limits.",
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "transport": {"type": "string"},
                    "live": {"type": "boolean"},
                    "storage": {"type": "string"},
                    "read_only": {"type": "boolean"},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "filters": {"type": "array", "items": {"type": "string"}},
                    "limits": {"type": "object"},
                    "extraction": {"type": "object"},
                },
            }
        ),
    },
    {
        "name": "search_filings",
        "description": search_filings.__doc__ or "",
        "annotations": dict(_TOOL_ANNOTATIONS),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "YYYY-MM-DD or DD/MM/YYYY"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD or DD/MM/YYYY"},
                "stock_code": {
                    "type": "string",
                    "description": "Optional exact HKEx stock code, e.g. 01461",
                },
                "title_query": {
                    "type": "string",
                    "description": "Optional case-insensitive substring of the filing title",
                },
                "document_type": {
                    "type": "string",
                    "description": "Optional exact file type, e.g. PDF, HTML, XLS, DOC",
                },
                "category": {
                    "type": "string",
                    "description": "Optional case-insensitive substring of the headline category",
                },
                "stock_name": {
                    "type": "string",
                    "description": "Optional case-insensitive substring of the stock short name",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_MAX_RESULTS,
                    "description": "Maximum filings to fetch and return (default 50, cap 200)",
                },
            },
            "required": ["from_date", "to_date"],
            "additionalProperties": False,
        },
        "outputSchema": _structured_output(
            {
                "type": "object",
                "description": "The filings matching the window and filters.",
                "properties": {
                    "from": {"type": "string", "description": "Window start, YYYY-MM-DD"},
                    "to": {"type": "string", "description": "Window end, YYYY-MM-DD"},
                    "count": {"type": "integer", "description": "Filings returned"},
                    "total_reported": {
                        "type": ["integer", "null"],
                        "description": "HKEx-reported total for the window, when known",
                    },
                    "filters": {"type": "object", "description": "The filters applied"},
                    "filings": {
                        "type": "array",
                        "description": "One entry per filing",
                        "items": _FILING_ENTRY,
                    },
                },
                "required": ["from", "to", "count", "filings"],
            }
        ),
    },
    {
        "name": "list_filing_facets",
        "description": list_filing_facets.__doc__ or "",
        "annotations": dict(_TOOL_ANNOTATIONS),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "YYYY-MM-DD or DD/MM/YYYY"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD or DD/MM/YYYY"},
                "stock_code": {
                    "type": "string",
                    "description": "Optional exact HKEx stock code to narrow the facets",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_MAX_RESULTS,
                    "description": "Maximum filings to scan (default 50, cap 200)",
                },
            },
            "required": ["from_date", "to_date"],
            "additionalProperties": False,
        },
        "outputSchema": _structured_output(
            {
                "type": "object",
                "description": "Counts of the window's categories, document types, and stock codes.",
                "properties": {
                    "from": {"type": "string", "description": "Window start, YYYY-MM-DD"},
                    "to": {"type": "string", "description": "Window end, YYYY-MM-DD"},
                    "count": {"type": "integer", "description": "Filings scanned"},
                    "total_reported": {"type": ["integer", "null"]},
                    "stock_code_filter": {"type": "string"},
                    "distinct_categories": {"type": "integer"},
                    "distinct_document_types": {"type": "integer"},
                    "distinct_stock_codes": {"type": "integer"},
                    "categories": {"type": "array", "items": _FACET_ENTRY},
                    "document_types": {"type": "array", "items": _FACET_ENTRY},
                    "stock_codes": {"type": "array", "items": _FACET_ENTRY},
                },
                "required": ["from", "to", "count"],
            }
        ),
    },
    {
        "name": "get_filing",
        "description": get_filing.__doc__ or "",
        "annotations": dict(_TOOL_ANNOTATIONS),
        "inputSchema": {
            "type": "object",
            "properties": {
                "link": {"type": "string", "description": "HKEx document URL"},
                "extract": {"type": "boolean", "description": "Extract text and tables"},
            },
            "required": ["link"],
            "additionalProperties": False,
        },
        "outputSchema": _structured_output(
            {
                "type": "object",
                "description": "The downloaded document, with text and tables when extract is true.",
                "properties": {
                    "link": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "content_type": {"type": "string"},
                    "document_text": {"type": "string", "description": "Extracted Markdown text"},
                    "text_length": {"type": "integer"},
                    "text_truncated": {"type": "boolean"},
                    "tables": {"type": "array", "items": {"type": "object"}},
                    "tables_omitted": {"type": "integer"},
                },
                "required": ["link", "size_bytes"],
            }
        ),
    },
]


# ---------------------------------------------------------------------------
# Legacy Streamable HTTP JSON-RPC (hand-rolled, lifespan-free)
#
# The mcp SDK's Streamable HTTP server needs its ASGI lifespan to start a task group,
# which serverless platforms do not reliably run. This transport implements the same
# legacy wire format directly, so it works as a plain request handler.
# ---------------------------------------------------------------------------
JSON_HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}
JSONRPC_VERSION = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601


class RpcResponse(NamedTuple):
    """A JSON-RPC HTTP response: status, headers, and an optional JSON body."""

    status: int
    headers: Dict[str, str]
    body: Any


def _jsonrpc_ok(message_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "result": result}


def _jsonrpc_error(message_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def server_metadata() -> Dict[str, Any]:
    """The ``serverInfo`` block shared by ``initialize`` and ``get_server_info``."""
    return {"name": SERVER_NAME, "version": __version__}


def _capabilities() -> Dict[str, Any]:
    return {"tools": {"listChanged": False}}


def _negotiate_protocol(requested: Any) -> str:
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return PROTOCOL_VERSION


def _tool_result(value: Any) -> Dict[str, Any]:
    text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": value},
        "isError": False,
    }


def _tool_error(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _handle_message(message: Any) -> Optional[Dict[str, Any]]:
    """Return a JSON-RPC response object, or ``None`` for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC_VERSION:
        return _jsonrpc_error(None, INVALID_REQUEST, "Invalid Request")
    method = message.get("method")
    message_id = message.get("id")
    if not isinstance(method, str):
        return _jsonrpc_error(message_id, INVALID_REQUEST, "Invalid Request")
    is_notification = message_id is None

    if method == "initialize":
        params = message.get("params") or {}
        result = {
            "protocolVersion": _negotiate_protocol(params.get("protocolVersion")),
            "capabilities": _capabilities(),
            "serverInfo": server_metadata(),
            "instructions": INSTRUCTIONS,
        }
        return _jsonrpc_ok(message_id, result)
    if method == "ping":
        return None if is_notification else _jsonrpc_ok(message_id, {})
    if method == "tools/list":
        return None if is_notification else _jsonrpc_ok(message_id, {"tools": TOOL_SCHEMAS})
    if method == "tools/call":
        if is_notification:
            return None
        params = message.get("params") or {}
        arguments = params.get("arguments") or {}
        try:
            value = handle_tool(
                str(params.get("name")), arguments if isinstance(arguments, dict) else {}
            )
        except McpError as exc:
            return _jsonrpc_ok(message_id, _tool_error(str(exc)))
        except Exception as exc:  # noqa: BLE001 - never leak a traceback
            return _jsonrpc_ok(message_id, _tool_error(f"tool failed: {type(exc).__name__}"))
        return _jsonrpc_ok(message_id, _tool_result(value))
    if method.startswith("notifications/"):
        return None
    # The server advertises no resources or prompts, but some clients probe these
    # unconditionally; answer with empty lists instead of "method not found".
    if method == "resources/list":
        return None if is_notification else _jsonrpc_ok(message_id, {"resources": []})
    if method == "resources/templates/list":
        return None if is_notification else _jsonrpc_ok(message_id, {"resourceTemplates": []})
    if method == "prompts/list":
        return None if is_notification else _jsonrpc_ok(message_id, {"prompts": []})
    return _jsonrpc_error(message_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def handle_jsonrpc(message: Any) -> RpcResponse:
    """Handle one MCP JSON-RPC message (or a batch) over legacy Streamable HTTP.

    Notifications return ``202`` with no body; requests return ``200`` with JSON. This is
    transport-only, so it never needs the SDK, a session, or a lifespan hook.
    """
    if isinstance(message, list):
        responses = [r for r in (_handle_message(item) for item in message) if r is not None]
        if not responses:
            return RpcResponse(202, {}, None)
        return RpcResponse(200, dict(JSON_HEADERS), responses)
    response = _handle_message(message)
    if response is None:
        return RpcResponse(202, {}, None)
    return RpcResponse(200, dict(JSON_HEADERS), response)


def parse_error_response() -> RpcResponse:
    """The response for a body that is not valid JSON."""
    return RpcResponse(200, dict(JSON_HEADERS), _jsonrpc_error(None, PARSE_ERROR, "Parse error"))


# ---------------------------------------------------------------------------
# Server assembly
# ---------------------------------------------------------------------------
def build_server() -> Any:
    """Build the FastMCP server with the four live, read-only tools registered."""
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            'MCP support is not installed; install with: pip install "hkex-filing-scraper[mcp]"'
        )
    annotations = ToolAnnotations(  # type: ignore[misc]
        readOnlyHint=True, destructiveHint=False, openWorldHint=True
    )
    server = FastMCP(  # type: ignore[misc]
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        streamable_http_path=STREAMABLE_HTTP_PATH,
    )
    for tool in TOOLS:
        server.add_tool(tool, annotations=annotations)
    return server


def build_asgi():
    """Return the stateless Streamable HTTP ASGI app for serverless hosting.

    ``stateless_http`` and ``json_response`` are set on the server, so every request gets a
    fresh transport and a single JSON response (no session map, no SSE stream).
    """
    return build_server().streamable_http_app()
