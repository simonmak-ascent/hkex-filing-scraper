"""Optional PostgreSQL persistence sink.

This module mirrors the SurrealDB persistence surface onto PostgreSQL. It is
import-safe when the optional ``psycopg`` driver is not installed: the module
still imports, :func:`postgres_available` returns ``False``, and every write
entry point returns a ``POSTGRES_DRIVER_MISSING`` failure code instead of
raising. Callers therefore never need to guard imports themselves.

All statements use bound parameters; no value is ever interpolated into SQL
text. Connection details are read from :mod:`hkex_scraper.config` and are never
logged.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import (
    POSTGRES_FTS_INDEX,
    SCRAPER_HEARTBEAT_FILE,
    SCRAPE_LOCK_KEY,
    POSTGRES_MAX_POOL,
    POSTGRES_MIN_POOL,
    POSTGRES_SCHEMA,
    postgres_conninfo,
)
from .sinks.base import AGGREGATE_GROUPS, SEARCH_COLUMNS, FilingQuery
from .utils import log

# ---------------------------------------------------------------------------
# Optional driver (guarded import)
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised via monkeypatching in tests
    import psycopg  # type: ignore
    from psycopg.rows import dict_row  # type: ignore
    from psycopg.types.json import Jsonb  # type: ignore

    _PSYCOPG_AVAILABLE = True
except Exception:  # pragma: no cover - depends on environment
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore
    Jsonb = None  # type: ignore
    _PSYCOPG_AVAILABLE = False

try:  # pragma: no cover - exercised via monkeypatching in tests
    from psycopg_pool import ConnectionPool  # type: ignore

    _POOL_AVAILABLE = True
except Exception:  # pragma: no cover - depends on environment
    ConnectionPool = None  # type: ignore
    _POOL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Error codes (see contracts/primary-endpoint.md)
# ---------------------------------------------------------------------------
ERR_NONE = ""
ERR_DISABLED = "POSTGRES_DISABLED"
ERR_DRIVER_MISSING = "POSTGRES_DRIVER_MISSING"
ERR_DSN_MISSING = "POSTGRES_DSN_MISSING"
ERR_CONNECT_FAILED = "POSTGRES_CONNECT_FAILED"
ERR_SCHEMA_ERROR = "POSTGRES_SCHEMA_ERROR"
ERR_WRITE_ERROR = "POSTGRES_WRITE_ERROR"
ERR_PAYLOAD_ERROR = "POSTGRES_PAYLOAD_ERROR"
ERR_PARITY_MISMATCH = "POSTGRES_PARITY_MISMATCH"

_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Credential patterns scrubbed from any error text before logging.
_DSN_CRED_RE = re.compile(r"(?i)(password|pwd)=([^\s]+)")
_URL_CRED_RE = re.compile(r"(?i)(://[^:/@\s]+):([^@\s]+)@")


# ---------------------------------------------------------------------------
# Availability and capability
# ---------------------------------------------------------------------------


def postgres_available() -> bool:
    """True when the driver is importable and a connection string is configured."""
    return bool(_PSYCOPG_AVAILABLE) and bool(postgres_conninfo())


def driver_installed() -> bool:
    """True when the optional ``psycopg`` driver can be imported."""
    return bool(_PSYCOPG_AVAILABLE)


def _redact(text: str) -> str:
    """Remove credentials from error text before it is logged."""
    if not text:
        return ""
    redacted = _DSN_CRED_RE.sub(r"\1=***", text)
    redacted = _URL_CRED_RE.sub(r"\1:***@", redacted)
    return redacted


def _safe_schema() -> str:
    """Return a validated schema identifier, falling back to ``public``."""
    value = (POSTGRES_SCHEMA or "public").strip()
    return value if _SCHEMA_RE.match(value) else "public"


def _jsonb(value: Any) -> Any:
    """Adapt a Python value for a ``jsonb`` column when the driver is present."""
    if _PSYCOPG_AVAILABLE and Jsonb is not None and value is not None:
        return Jsonb(value)
    return value


# ---------------------------------------------------------------------------
# Connection pool (lazy, thread-safe)
# ---------------------------------------------------------------------------
_pool = None
_pool_lock = threading.Lock()


def _get_pool():  # pragma: no cover - requires a live driver
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not (_PSYCOPG_AVAILABLE and _POOL_AVAILABLE) or ConnectionPool is None:
                    raise RuntimeError(ERR_DRIVER_MISSING)
                conninfo = postgres_conninfo()
                if not conninfo:
                    raise RuntimeError(ERR_DSN_MISSING)
                kwargs: Dict[str, Any] = {
                    "min_size": POSTGRES_MIN_POOL,
                    "max_size": POSTGRES_MAX_POOL,
                    "open": True,
                }
                schema = _safe_schema()
                if schema != "public":
                    kwargs["kwargs"] = {"options": f"-c search_path={schema}"}
                _pool = ConnectionPool(conninfo=conninfo, **kwargs)
    return _pool


# ---------------------------------------------------------------------------
# Single-producer lock and heartbeat
# ---------------------------------------------------------------------------

#: Held for the lifetime of the process; a session-level advisory lock dies with it.
_lock_conn = None
_lock_guard = threading.Lock()


def try_acquire_scrape_lock() -> Tuple[bool, str]:
    """Take the corpus scrape lock.

    Returns ``(held, detail)``. A False result means another node is scraping: the
    caller should exit quietly, because the corpus is idempotent and the other run
    will ingest the same filings. When the driver or the database is unavailable the
    lock degrades to "held" — a missing lock must not stop a scrape that would
    otherwise work.
    """
    global _lock_conn
    if not postgres_available():
        return True, "postgres driver unavailable; lock skipped"
    with _lock_guard:
        if _lock_conn is not None:
            return True, "already held by this process"
        try:
            conn = psycopg.connect(postgres_conninfo(), autocommit=True)
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (SCRAPE_LOCK_KEY,))
                row = cur.fetchone()
            if not (row and row[0]):
                conn.close()
                return False, "another producer holds the scrape lock"
            _lock_conn = conn
            return True, "locked"
        except Exception as exc:  # noqa: BLE001 - never block a scrape on the lock
            log(f"  WARNING: scrape lock unavailable ({type(exc).__name__}); continuing unlocked")
            return True, f"lock unavailable: {type(exc).__name__}"


def release_scrape_lock() -> None:
    """Release the scrape lock (idempotent)."""
    global _lock_conn
    with _lock_guard:
        if _lock_conn is None:
            return
        try:
            with _lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (SCRAPE_LOCK_KEY,))
        except Exception:  # noqa: BLE001 - closing the connection releases it anyway
            pass
        finally:
            try:
                _lock_conn.close()
            except Exception:  # noqa: BLE001
                pass
            _lock_conn = None


def _write_heartbeat_file(phase: str, detail: str) -> None:
    """Best-effort timestamp file for the watchdog, which has no database client."""
    if not SCRAPER_HEARTBEAT_FILE:
        return
    try:
        path = Path(SCRAPER_HEARTBEAT_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(f"{stamp} {phase} {detail}".rstrip() + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 - telemetry must not break the run
        pass


def write_heartbeat(run_id: str, phase: str, detail: str = "") -> Tuple[bool, str]:
    """Record what this run is doing, so a hung run is visible to the watchdog.

    One row, upserted in place. Deliberately best-effort: a heartbeat failure must
    never fail a scrape.
    """
    _write_heartbeat_file(phase, detail)

    if not postgres_available():
        return False, ERR_DRIVER_MISSING
    try:
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scraper_heartbeat (id, run_id, phase, detail, updated_at)"
                    " VALUES (1, %s, %s, %s, now())"
                    " ON CONFLICT (id) DO UPDATE SET"
                    " run_id = EXCLUDED.run_id, phase = EXCLUDED.phase,"
                    " detail = EXCLUDED.detail, updated_at = now()",
                    (run_id, phase, detail or None),
                )
        return True, ""
    except Exception as exc:  # noqa: BLE001 - telemetry must not break the run
        return False, f"{type(exc).__name__}: {exc}"


def close_pool() -> None:
    """Close the shared connection pool if one is open."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception as exc:  # pragma: no cover - best effort
            log(f"  PostgreSQL pool close warning: {_redact(str(exc))[:200]}")
        finally:
            _pool = None


def _run(
    sql: str,
    params: Optional[Any] = None,
    many: bool = False,
) -> Tuple[bool, str, int]:
    """Execute one statement on a pooled connection.

    Returns ``(ok, error_code, rowcount)``. Never raises; the caller decides
    what a failure means for the run.
    """
    if not _PSYCOPG_AVAILABLE:
        return False, ERR_DRIVER_MISSING, 0
    if not postgres_conninfo():
        return False, ERR_DSN_MISSING, 0
    try:
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                if many:
                    cur.executemany(sql, params or [])
                    # executemany rowcount is not reliable across psycopg releases,
                    # so callers treat this as rows attempted, not rows affected.
                    rowcount = len(params or [])
                elif params is not None:
                    cur.execute(sql, params)
                    rowcount = cur.rowcount if (cur.rowcount or -1) >= 0 else 0
                else:
                    cur.execute(sql)
                    rowcount = 0
            conn.commit()
        return True, ERR_NONE, rowcount
    except Exception as exc:  # noqa: BLE001 - every failure is surfaced via the code
        return False, _redact(str(exc)) or ERR_WRITE_ERROR, 0


def _run_counting(sql: str, rows: List[Any]) -> Tuple[bool, str, int]:
    """Execute a ``RETURNING`` statement per row and count the returned rows.

    Used for ``ON CONFLICT DO NOTHING`` inserts, where the number of rows
    actually inserted (conflicts excluded) is what matters. Per-row ``rowcount``
    is reliable, unlike ``executemany`` rowcount.
    """
    if not _PSYCOPG_AVAILABLE:
        return False, ERR_DRIVER_MISSING, 0
    if not postgres_conninfo():
        return False, ERR_DSN_MISSING, 0
    try:
        total = 0
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(sql, row)
                    if cur.rowcount and cur.rowcount > 0:
                        total += cur.rowcount
            conn.commit()
        return True, ERR_NONE, total
    except Exception as exc:  # noqa: BLE001
        return False, _redact(str(exc)) or ERR_WRITE_ERROR, 0


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_EXCHANGE_FILING_DDL = """
CREATE TABLE IF NOT EXISTS exchange_filing (
    filing_id             text PRIMARY KEY,
    company_ticker        text NOT NULL,
    stock_code            text NOT NULL,
    stock_name            text,
    exchange              text NOT NULL,
    filing_type           text NOT NULL,
    filing_subtype        text,
    filing_category       text,
    title                 text,
    filing_date           timestamptz,
    document_url          text,
    referenced_tickers    text[],
    source                text NOT NULL,
    updated_at            timestamptz NOT NULL DEFAULT now(),
    document_size         bigint,
    document_type         text,
    document_hash         text,
    document_sha256       text,
    document_text         text,
    document_text_len     integer,
    document_tables       jsonb,
    document_table_cnt    integer,
    document_status       text,
    document_status_reason text,
    CONSTRAINT chk_ef_doc_status
        CHECK (document_status IS NULL OR document_status IN ('processed','skipped','failed'))
)
""".strip()

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_ticker ON exchange_filing (company_ticker)",
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_stockcode ON exchange_filing (stock_code)",
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_date ON exchange_filing (filing_date)",
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_type ON exchange_filing (filing_type)",
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_source ON exchange_filing (source)",
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_docstatus ON exchange_filing (document_status)",
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_category ON exchange_filing (filing_category)",
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_ref_tickers "
    "ON exchange_filing USING GIN (referenced_tickers)",
    "CREATE INDEX IF NOT EXISTS idx_pg_ef_doc_tables "
    "ON exchange_filing USING GIN (document_tables jsonb_path_ops)",
    "CREATE INDEX IF NOT EXISTS idx_pg_cov_run ON scrape_coverage (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_pg_hf_filing ON has_filing (filing_id)",
    "CREATE INDEX IF NOT EXISTS idx_pg_rf_company ON references_filing (company_id)",
]


def _build_postgres_schema_sql() -> List[str]:
    """Return the full mirrored DDL as a list of idempotent statements."""
    statements = [
        _EXCHANGE_FILING_DDL,
        (
            "CREATE TABLE IF NOT EXISTS scrape_coverage ("
            " id bigserial PRIMARY KEY,"
            " chunk_from timestamptz NOT NULL,"
            " chunk_to timestamptz NOT NULL,"
            " api_count integer NOT NULL DEFAULT 0,"
            " ingested_count integer NOT NULL DEFAULT 0,"
            " unique_count integer NOT NULL DEFAULT 0,"
            " run_id text NOT NULL,"
            ' "timestamp" timestamptz NOT NULL DEFAULT now(),'
            " CONSTRAINT uq_cov_chunk_run UNIQUE (chunk_from, chunk_to, run_id)"
            ")"
        ),
        (
            "CREATE TABLE IF NOT EXISTS has_filing ("
            " company_id text NOT NULL,"
            " filing_id text NOT NULL REFERENCES exchange_filing(filing_id) ON DELETE CASCADE,"
            " created_at timestamptz NOT NULL DEFAULT now(),"
            " PRIMARY KEY (company_id, filing_id)"
            ")"
        ),
        (
            "CREATE TABLE IF NOT EXISTS scraper_heartbeat ("
            " id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),"
            " run_id text NOT NULL,"
            " phase text NOT NULL,"
            " detail text,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ),
        (
            "DO $$ BEGIN"
            " IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grp_sender') THEN"
            " EXECUTE 'GRANT SELECT ON scraper_heartbeat TO grp_sender';"
            " END IF;"
            " IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grp_tracker') THEN"
            " EXECUTE 'GRANT SELECT ON scraper_heartbeat TO grp_tracker';"
            " END IF;"
            " END $$"
        ),
        (
            "CREATE TABLE IF NOT EXISTS references_filing ("
            " filing_id text NOT NULL REFERENCES exchange_filing(filing_id) ON DELETE CASCADE,"
            " company_id text NOT NULL,"
            " source text,"
            " created_at timestamptz NOT NULL DEFAULT now(),"
            " PRIMARY KEY (filing_id, company_id)"
            ")"
        ),
    ]
    statements.extend(_INDEXES)
    return statements


def initialize_postgres_schema() -> Tuple[bool, str]:
    """Create the mirrored schema if absent. Safe to re-run; never destructive."""
    if not _PSYCOPG_AVAILABLE:
        return False, ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return False, ERR_DSN_MISSING

    # On an already-initialised database these statements are no-ops, but
    # PostgreSQL authorises CREATE INDEX against the table owner before it
    # honours IF NOT EXISTS. A producer that holds only DML grants (the RDS
    # restore left exchange_filing owned by svc_admin) must not fail the whole
    # scrape for that: the run reads and writes data, it does not manage the
    # schema.
    schema_ready = _run("SELECT 1 FROM exchange_filing LIMIT 1")[0]

    for statement in _build_postgres_schema_sql():
        ok, code, _ = _run(statement)
        if not ok:
            if schema_ready and "must be owner of" in (code or "").lower():
                log("  PostgreSQL schema already present; skipped DDL that needs table ownership")
                continue
            return False, code or ERR_SCHEMA_ERROR
    return True, ERR_NONE


_SEARCH_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS pg_trgm"


def _build_search_index_sql(schema: str = "public") -> List[str]:
    """Optional ``pg_trgm`` GIN indexes that accelerate substring search.

    ``gin_trgm_ops`` supports ``LIKE``/``ILIKE`` with a ``%substring%`` pattern, which is
    exactly the shape of ``search_filings``/``search_documents`` predicates. The operator
    class is schema-qualified because the connection's ``search_path`` is the sink schema,
    not the schema the extension was installed into.
    """
    opclass = f"{schema}.gin_trgm_ops"
    return [
        "CREATE INDEX IF NOT EXISTS idx_pg_ef_title_trgm ON exchange_filing "
        f"USING gin (lower(title) {opclass})",
        "CREATE INDEX IF NOT EXISTS idx_pg_ef_doctext_trgm ON exchange_filing "
        f"USING gin (lower(document_text) {opclass})",
    ]


def _trgm_schema() -> str:
    """Return the schema the ``pg_trgm`` extension lives in (default ``public``)."""
    value, code = _fetch_scalar(
        "SELECT n.nspname FROM pg_extension e "
        "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'"
    )
    name = str(value) if not code and value else "public"
    return name if _SCHEMA_RE.match(name) else "public"


def ensure_search_indexes() -> Tuple[bool, str]:
    """Create the optional search indexes. Best effort: never fails schema init.

    A database user without ``CREATE EXTENSION``/``CREATE INDEX`` privilege logs a warning
    and search falls back to a sequential scan. Returns ``(ok, first_error_code)``.
    """
    if not POSTGRES_FTS_INDEX:
        return True, ERR_NONE
    if not _PSYCOPG_AVAILABLE:
        return False, ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return False, ERR_DSN_MISSING
    ok, code, _ = _run(_SEARCH_EXTENSION_SQL)
    if not ok:
        log(f"  PostgreSQL search index warning: {_redact(code)[:200]}")
        return False, code
    schema = _trgm_schema()
    first_error = ""
    for statement in _build_search_index_sql(schema):
        ok, code, _ = _run(statement)
        if not ok:
            first_error = first_error or code
            log(f"  PostgreSQL search index warning: {_redact(code)[:200]}")
    return (not first_error), first_error


# ---------------------------------------------------------------------------
# Filing metadata upsert (does NOT touch document_* columns)
# ---------------------------------------------------------------------------
_FILING_COLUMNS = [
    "filing_id",
    "company_ticker",
    "stock_code",
    "stock_name",
    "exchange",
    "filing_type",
    "filing_subtype",
    "filing_category",
    "title",
    "filing_date",
    "document_url",
    "referenced_tickers",
    "source",
    "updated_at",
]


def _build_upsert_filings_sql() -> str:
    placeholders = ", ".join(["%s"] * len(_FILING_COLUMNS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _FILING_COLUMNS if c != "filing_id")
    return (
        f"INSERT INTO exchange_filing ({', '.join(_FILING_COLUMNS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (filing_id) DO UPDATE SET {updates}"
    )


def _filing_row(filing: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        filing.get("filing_id", ""),
        filing.get("company_ticker", ""),
        filing.get("stock_code", ""),
        filing.get("stock_name"),
        filing.get("exchange", "HK"),
        filing.get("filing_type", "Other"),
        filing.get("filing_subtype"),
        filing.get("filing_category"),
        filing.get("title"),
        filing.get("filing_date"),
        filing.get("document_url"),
        filing.get("referenced_tickers"),
        filing.get("source", "HKEx"),
        filing.get("updated_at") or datetime.now(timezone.utc),
    )


def upsert_filings(filings: List[Dict[str, Any]]) -> Tuple[int, str]:
    """Upsert a batch of filing metadata. Returns ``(written, error_code)``."""
    if not _PSYCOPG_AVAILABLE:
        return 0, ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return 0, ERR_DSN_MISSING
    if not filings:
        return 0, ERR_NONE
    try:
        rows = [_filing_row(f) for f in filings]
    except Exception as exc:  # noqa: BLE001
        return 0, _redact(str(exc)) or ERR_PAYLOAD_ERROR
    ok, code, affected = _run(_build_upsert_filings_sql(), rows, many=True)
    if not ok:
        return 0, code
    return affected, ERR_NONE


# ---------------------------------------------------------------------------
# Document payload upsert
# ---------------------------------------------------------------------------
_DOCUMENT_COLUMNS = [
    "document_size",
    "document_type",
    "document_hash",
    "document_sha256",
    "document_text",
    "document_text_len",
    "document_tables",
    "document_table_cnt",
    "document_status",
    "document_status_reason",
]


def _build_upsert_document_sql() -> str:
    sets = ", ".join(f"{c} = %s" for c in _DOCUMENT_COLUMNS)
    return f"UPDATE exchange_filing SET {sets}, updated_at = %s WHERE filing_id = %s"


def upsert_document(filing_id: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
    """Write the document payload for an existing filing. Returns ``(ok, error_code)``."""
    if not _PSYCOPG_AVAILABLE:
        return False, ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return False, ERR_DSN_MISSING
    params = [
        payload.get("document_size"),
        payload.get("document_type"),
        payload.get("document_hash"),
        payload.get("document_sha256"),
        payload.get("document_text"),
        payload.get("document_text_len"),
        _jsonb(payload.get("document_tables")),
        payload.get("document_table_cnt"),
        payload.get("document_status"),
        payload.get("document_status_reason"),
        datetime.now(timezone.utc),
        filing_id,
    ]
    ok, code, rowcount = _run(_build_upsert_document_sql(), params)
    if not ok:
        return False, code
    if rowcount == 0:
        return False, ERR_WRITE_ERROR
    return True, ERR_NONE


def mark_document_status(filing_id: str, status: str, reason: str = "") -> Tuple[bool, str]:
    """Update only the document status columns (used for skipped/failed filings)."""
    if not _PSYCOPG_AVAILABLE:
        return False, ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return False, ERR_DSN_MISSING
    sql = (
        "UPDATE exchange_filing SET document_status = %s, document_status_reason = %s, "
        "updated_at = %s WHERE filing_id = %s"
    )
    ok, code, rowcount = _run(sql, [status, reason or "", datetime.now(timezone.utc), filing_id])
    if not ok:
        return False, code
    if rowcount == 0:
        return False, ERR_WRITE_ERROR
    return True, ERR_NONE


# ---------------------------------------------------------------------------
# Coverage upsert
# ---------------------------------------------------------------------------
_COVERAGE_COLUMNS = [
    "chunk_from",
    "chunk_to",
    "api_count",
    "ingested_count",
    "unique_count",
    "run_id",
    "timestamp",
]
_COVERAGE_KEY = ("chunk_from", "chunk_to", "run_id")


def _build_insert_coverage_sql() -> str:
    placeholders = ", ".join(["%s"] * len(_COVERAGE_COLUMNS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COVERAGE_COLUMNS if c not in _COVERAGE_KEY)
    return (
        f"INSERT INTO scrape_coverage ({', '.join(_COVERAGE_COLUMNS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (chunk_from, chunk_to, run_id) DO UPDATE SET {updates}"
    )


def _coverage_row(chunk: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        chunk.get("chunk_from"),
        chunk.get("chunk_to"),
        chunk.get("api_count", 0),
        chunk.get("ingested_count", 0),
        chunk.get("unique_count", 0),
        chunk.get("run_id", ""),
        chunk.get("timestamp") or datetime.now(timezone.utc),
    )


def insert_coverage(chunk: Dict[str, Any]) -> Tuple[bool, str]:
    """Upsert one coverage row. Returns ``(ok, error_code)``."""
    if not _PSYCOPG_AVAILABLE:
        return False, ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return False, ERR_DSN_MISSING
    ok, code, _ = _run(_build_insert_coverage_sql(), _coverage_row(chunk))
    if not ok:
        return False, code
    return True, ERR_NONE


# ---------------------------------------------------------------------------
# Graph edges
# ---------------------------------------------------------------------------
_EDGE_KINDS = ("has_filing", "references_filing")


def _build_upsert_edges_sql(kind: str) -> str:
    if kind == "has_filing":
        return (
            "INSERT INTO has_filing (company_id, filing_id) VALUES (%s, %s) "
            "ON CONFLICT (company_id, filing_id) DO NOTHING"
        )
    if kind == "references_filing":
        return (
            "INSERT INTO references_filing (filing_id, company_id, source) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (filing_id, company_id) DO NOTHING"
        )
    raise ValueError(f"unknown edge kind: {kind}")


def _edge_row(kind: str, edge: Dict[str, Any]) -> Tuple[Any, ...]:
    if kind == "has_filing":
        return (edge.get("company_id", ""), edge.get("filing_id", ""))
    return (
        edge.get("filing_id", ""),
        edge.get("company_id", ""),
        edge.get("source", "title_extraction"),
    )


def upsert_edges(edges: List[Dict[str, Any]], kind: str) -> Tuple[int, str]:
    """Insert edges idempotently; conflicts are a no-op.

    Returns ``(created, error_code)`` where ``created`` is the number of rows
    actually inserted (conflicting rows affect 0 rows and are excluded).
    """
    if not _PSYCOPG_AVAILABLE:
        return 0, ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return 0, ERR_DSN_MISSING
    if not edges:
        return 0, ERR_NONE
    if kind not in _EDGE_KINDS:
        return 0, ERR_PAYLOAD_ERROR
    rows = [_edge_row(kind, e) for e in edges]
    ok, code, created = _run_counting(_build_upsert_edges_sql(kind) + " RETURNING 1", rows)
    if not ok:
        return 0, code
    return created, ERR_NONE


# ---------------------------------------------------------------------------
# Parity / reporting helpers
# ---------------------------------------------------------------------------


def _fetch_scalar(sql: str, params: Optional[Any] = None) -> Tuple[Optional[Any], str]:
    """Execute a query and return the first column of the first row."""
    if not _PSYCOPG_AVAILABLE:
        return None, ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return None, ERR_DSN_MISSING
    try:
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                if params is not None:
                    cur.execute(sql, params)
                else:
                    cur.execute(sql)
                row = cur.fetchone()
        return (row[0] if row else 0), ERR_NONE
    except Exception as exc:  # noqa: BLE001
        return None, _redact(str(exc)) or ERR_WRITE_ERROR


def read_filing_digests() -> Tuple[List[Dict[str, Any]], str]:
    """Every filing id with its integrity hash, ordered by id."""
    rows, error = _fetch_all(
        "SELECT filing_id, document_sha256 FROM exchange_filing ORDER BY filing_id"
    )
    if error:
        return [], error
    return [
        {
            "filing_id": row.get("filing_id", ""),
            "document_sha256": row.get("document_sha256") or "",
        }
        for row in rows
    ], ERR_NONE


def count_filings() -> Tuple[int, str]:
    """Return the number of filing rows in PostgreSQL as ``(count, error_code)``."""
    value, code = _fetch_scalar("SELECT count(*) FROM exchange_filing")
    if code:
        return 0, code
    try:
        return int(value or 0), ERR_NONE
    except (TypeError, ValueError):
        return 0, ERR_WRITE_ERROR


_EDGE_TABLES = {"has_filing": "has_filing", "references_filing": "references_filing"}


def count_edges(kind: str) -> Tuple[int, str]:
    """Return the number of rows in an edge table as ``(count, error_code)``."""
    table = _EDGE_TABLES.get(kind)
    if not table:
        return 0, ERR_PAYLOAD_ERROR
    value, code = _fetch_scalar(f"SELECT count(*) FROM {table}")
    if code:
        return 0, code
    try:
        return int(value or 0), ERR_NONE
    except (TypeError, ValueError):
        return 0, ERR_WRITE_ERROR


# ---------------------------------------------------------------------------
# Read helpers (used when PostgreSQL is the operational store)
# ---------------------------------------------------------------------------


def _fetch_all(sql: str, params: Optional[Any] = None) -> Tuple[List[Dict[str, Any]], str]:
    """Execute a query and return all rows as dicts."""
    if not _PSYCOPG_AVAILABLE:
        return [], ERR_DRIVER_MISSING
    if not postgres_conninfo():
        return [], ERR_DSN_MISSING
    if dict_row is None:
        return [], ERR_DRIVER_MISSING
    try:
        with _get_pool().connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if params is not None:
                    cur.execute(sql, params)
                else:
                    cur.execute(sql)
                rows = cur.fetchall()
        return [dict(r) for r in rows], ERR_NONE
    except Exception as exc:  # noqa: BLE001
        return [], _redact(str(exc)) or ERR_WRITE_ERROR


def count_pending_filings() -> Tuple[int, str]:
    """Count filings with a document URL but no document status yet."""
    value, code = _fetch_scalar(
        "SELECT count(*) FROM exchange_filing "
        "WHERE document_status IS NULL AND document_url IS NOT NULL AND document_url <> ''"
    )
    if code:
        return 0, code
    try:
        return int(value or 0), ERR_NONE
    except (TypeError, ValueError):
        return 0, ERR_WRITE_ERROR


def fetch_pending_filings(limit: int) -> Tuple[List[Dict[str, Any]], str]:
    """Return up to *limit* filings that still need document processing."""
    sql = (
        "SELECT filing_id, document_url, filing_date FROM exchange_filing "
        "WHERE document_status IS NULL AND document_url IS NOT NULL AND document_url <> '' "
        "ORDER BY filing_date DESC NULLS LAST LIMIT %s"
    )
    return _fetch_all(sql, [limit])


def fetch_filing_ids_by_ticker(tickers: List[str]) -> Tuple[List[Dict[str, Any]], str]:
    """Return ``{company_ticker, filing_id}`` rows for the given tickers."""
    if not tickers:
        return [], ERR_NONE
    sql = "SELECT company_ticker, filing_id FROM exchange_filing WHERE company_ticker = ANY(%s)"
    return _fetch_all(sql, [list(tickers)])


def list_edges(
    kind: str, company_id: str, limit: int, offset: int
) -> Tuple[List[Dict[str, Any]], str]:
    """Return graph edges of *kind* for one company, paged."""
    table = _EDGE_TABLES.get(kind)
    if not table:
        return [], ERR_PAYLOAD_ERROR
    if not company_id:
        return [], ERR_NONE
    cols = "company_id, filing_id" if kind == "has_filing" else "filing_id, company_id, source"
    sql = (
        f"SELECT {cols} FROM {table} WHERE company_id = %s "
        f"ORDER BY filing_id ASC LIMIT %s OFFSET %s"
    )
    return _fetch_all(sql, [company_id, limit, offset])


def distinct_company_tickers() -> Tuple[List[str], str]:
    """Return every distinct non-null company ticker."""
    rows, code = _fetch_all(
        "SELECT DISTINCT company_ticker FROM exchange_filing WHERE company_ticker IS NOT NULL"
    )
    if code:
        return [], code
    return [r.get("company_ticker", "") for r in rows if r.get("company_ticker")], ERR_NONE


def fetch_titles(
    ticker_set: Optional[List[str]], offset: int, page_size: int, title_query: str = ""
) -> Tuple[List[Dict[str, Any]], str]:
    """Page through filings for cross-reference scanning or title search."""
    clauses: List[str] = []
    params: List[Any] = []
    if ticker_set:
        clauses.append("company_ticker = ANY(%s)")
        params.append(list(ticker_set))
    clauses.append("title IS NOT NULL")
    if title_query:
        clauses.append("LOWER(title) LIKE LOWER(%s)")
        params.append(f"%{title_query}%")
    sql = (
        "SELECT filing_id, title, stock_code, company_ticker FROM exchange_filing "
        f"WHERE {' AND '.join(clauses)} ORDER BY filing_id ASC LIMIT %s OFFSET %s"
    )
    params.extend([page_size, offset])
    return _fetch_all(sql, params)


def fetch_filing_detail(filing_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return one filing's metadata plus its extracted document columns."""
    if not filing_id:
        return None, ERR_NONE
    columns = ", ".join((*_FILING_COLUMNS, *_DOCUMENT_COLUMNS))
    rows, code = _fetch_all(
        f"SELECT {columns} FROM exchange_filing WHERE filing_id = %s LIMIT 1", [filing_id]
    )
    if code:
        return None, code
    if not rows:
        return None, ERR_NONE
    return rows[0], ERR_NONE


# ---------------------------------------------------------------------------
# Composable search (bound parameters only)
# ---------------------------------------------------------------------------
def _search_where(query: FilingQuery) -> Tuple[List[str], List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if query.tickers:
        clauses.append("company_ticker = ANY(%s)")
        params.append(list(query.tickers))
    if query.stock_codes:
        clauses.append("stock_code = ANY(%s)")
        params.append(list(query.stock_codes))
    if query.title_query:
        clauses.append("LOWER(title) LIKE LOWER(%s)")
        params.append(f"%{query.title_query}%")
    if query.text_query:
        clauses.append("LOWER(document_text) LIKE LOWER(%s)")
        params.append(f"%{query.text_query}%")
    if query.filing_types:
        clauses.append("filing_type = ANY(%s)")
        params.append(list(query.filing_types))
    if query.filing_categories:
        clauses.append("filing_category = ANY(%s)")
        params.append(list(query.filing_categories))
    if query.document_status:
        real = [s for s in query.document_status if s and s != "unprocessed"]
        parts: List[str] = []
        if real:
            parts.append("document_status = ANY(%s)")
            params.append(real)
        if any(s == "unprocessed" for s in query.document_status):
            parts.append("document_status IS NULL")
        if parts:
            clauses.append("(" + " OR ".join(parts) + ")")
    if query.exchange:
        clauses.append("exchange = %s")
        params.append(query.exchange)
    if query.source:
        clauses.append("source = %s")
        params.append(query.source)
    if query.document_type:
        clauses.append("document_type = %s")
        params.append(query.document_type)
    if query.referenced_ticker:
        clauses.append("EXISTS (SELECT 1 FROM unnest(referenced_tickers) AS t WHERE t ILIKE %s)")
        params.append(f"%{query.referenced_ticker}%")
    if query.date_from:
        clauses.append("filing_date::date >= %s")
        params.append(query.date_from)
    if query.date_to:
        clauses.append("filing_date::date <= %s")
        params.append(query.date_to)
    return clauses, params


def _search_order(order_by: str) -> str:
    if order_by == "filing_date_asc":
        return "filing_date ASC NULLS LAST, filing_id ASC"
    if order_by == "title_asc":
        return "title ASC, filing_id ASC"
    if order_by == "filing_id_asc":
        return "filing_id ASC"
    return "filing_date DESC NULLS LAST, filing_id ASC"


def search_filings(query: FilingQuery, offset: int, limit: int) -> Tuple[List[Dict[str, Any]], str]:
    """Rich filing summaries matching *query*."""
    clauses, params = _search_where(query)
    where = " AND ".join(clauses) if clauses else "TRUE"
    columns = ", ".join(SEARCH_COLUMNS)
    sql = (
        f"SELECT {columns} FROM exchange_filing WHERE {where} "
        f"ORDER BY {_search_order(query.order_by)} LIMIT %s OFFSET %s"
    )
    return _fetch_all(sql, [*params, limit, offset])


def search_documents(
    query: FilingQuery, offset: int, limit: int
) -> Tuple[List[Dict[str, Any]], str]:
    """Full-text search over ``document_text`` with a snippet."""
    clauses, params = _search_where(query)
    where = " AND ".join(clauses) if clauses else "TRUE"
    columns = ", ".join(SEARCH_COLUMNS)
    snippet = (
        "substr(document_text, GREATEST(1, STRPOS(LOWER(document_text), LOWER(%s)) - 80), 320)"
    )
    sql = (
        f"SELECT {columns}, {snippet} AS snippet FROM exchange_filing WHERE {where} "
        f"ORDER BY {_search_order(query.order_by)} LIMIT %s OFFSET %s"
    )
    rows, code = _fetch_all(sql, [f"%{query.text_query}%", *params, limit, offset])
    if code:
        return [], code
    return rows, ERR_NONE


def aggregate_filings(group_by: str, query: FilingQuery) -> Tuple[List[Dict[str, Any]], str]:
    """Group matching filings by *group_by*; ``[{key, count}]`` by count desc."""
    if group_by not in AGGREGATE_GROUPS:
        return [], ERR_PAYLOAD_ERROR
    clauses, params = _search_where(query)
    where = " AND ".join(clauses) if clauses else "TRUE"
    sql = (
        f"SELECT {group_by} AS key, count(*) AS count FROM exchange_filing WHERE {where} "
        f"GROUP BY {group_by} ORDER BY count DESC, key ASC LIMIT 200"
    )
    rows, code = _fetch_all(sql, params)
    if code:
        return [], code
    return [{"key": r.get("key"), "count": int(r.get("count") or 0)} for r in rows], ERR_NONE


def count_matching(query: FilingQuery) -> Tuple[int, str]:
    """Count filings matching *query* without returning rows."""
    clauses, params = _search_where(query)
    where = " AND ".join(clauses) if clauses else "TRUE"
    value, code = _fetch_scalar(f"SELECT count(*) FROM exchange_filing WHERE {where}", params)
    if code:
        return 0, code
    try:
        return int(value or 0), ERR_NONE
    except (TypeError, ValueError):
        return 0, ERR_WRITE_ERROR


def list_companies(limit: int, offset: int, ticker: str = "") -> Tuple[List[Dict[str, Any]], str]:
    """Distinct companies with a display name and filing count, optionally ticker-filtered."""
    clauses = ["company_ticker IS NOT NULL", "company_ticker <> ''"]
    params: List[Any] = []
    if ticker:
        clauses.append("LOWER(company_ticker) LIKE LOWER(%s)")
        params.append(f"%{ticker}%")
    where = " AND ".join(clauses)
    sql = (
        "SELECT company_ticker, MAX(stock_name) AS stock_name, count(*) AS filing_count "
        f"FROM exchange_filing WHERE {where} "
        "GROUP BY company_ticker ORDER BY filing_count DESC, company_ticker ASC "
        "LIMIT %s OFFSET %s"
    )
    return _fetch_all(sql, [*params, limit, offset])


def fetch_coverage() -> Tuple[List[Dict[str, Any]], str]:
    """Return coverage rows newest-first for the ``--coverage-report`` command."""
    sql = (
        "SELECT chunk_from, chunk_to, api_count, ingested_count, unique_count, run_id, "
        '"timestamp" FROM scrape_coverage ORDER BY chunk_from DESC'
    )
    return _fetch_all(sql)
