"""Centralised configuration loaded from environment variables / .env file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote, unquote, urlparse

# ---------------------------------------------------------------------------
# Optional dotenv support (graceful if missing)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:

    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False


# Load .env from the current working directory (if present)
_env_path = Path.cwd() / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# ---------------------------------------------------------------------------
# SurrealDB connection (optional sink)
# ---------------------------------------------------------------------------
SURREAL_ENDPOINT: str = os.environ.get("SURREAL_ENDPOINT", "")
SURREAL_NS: str = os.environ.get("SURREAL_NAMESPACE", "default")
SURREAL_DB: str = os.environ.get("SURREAL_DATABASE", "default")
SURREAL_USER: str = os.environ.get("SURREAL_USERNAME", "root")
SURREAL_PASS: str = os.environ.get("SURREAL_PASSWORD", "")

# ---------------------------------------------------------------------------
# Database sink selection
# ---------------------------------------------------------------------------
# DATABASE_TARGET is an explicit, comma-separated list of sink ids, in order.
# Read routing uses the first configured sink that supports reads. There is no
# silent default: an unset or empty value is a configuration error surfaced by
# the CLI (see main._validate_env).
DATABASE_TARGET: str = os.environ.get("DATABASE_TARGET", "")


def parse_target(target: str) -> List[str]:
    """Parse a ``DATABASE_TARGET`` value into an ordered, de-duplicated id list."""
    seen: List[str] = []
    for raw in (target or "").split(","):
        sink_id = raw.strip().lower()
        if sink_id and sink_id not in seen:
            seen.append(sink_id)
    return seen


def sink_ids() -> List[str]:
    """Return the configured sink ids in order."""
    return parse_target(DATABASE_TARGET)


def apply_database_target(target: str) -> List[str]:
    """Re-resolve the sink selection at runtime (e.g. from a CLI override).

    Updates the module-level value in place and clears the constructed-sink
    cache so callers observe the override for the rest of the run.
    """
    global DATABASE_TARGET
    DATABASE_TARGET = target or ""
    from . import sinks

    sinks.reset()
    return sink_ids()


# ---------------------------------------------------------------------------
# PostgreSQL connection (optional sink)
# ---------------------------------------------------------------------------
POSTGRES_DSN: str = os.environ.get("POSTGRES_DSN", "")
POSTGRES_HOST: str = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT: str = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DATABASE: str = os.environ.get("POSTGRES_DATABASE", "")
POSTGRES_USER: str = os.environ.get("POSTGRES_USER", "")
POSTGRES_PASSWORD: str = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_SCHEMA: str = os.environ.get("POSTGRES_SCHEMA", "public")
POSTGRES_MIN_POOL: int = int(os.environ.get("POSTGRES_MIN_POOL", "1"))
POSTGRES_MAX_POOL: int = int(
    os.environ.get("POSTGRES_MAX_POOL", os.environ.get("MAX_DOWNLOAD_WORKERS", "15"))
)
# Create the optional pg_trgm GIN indexes that accelerate substring search on title
# and document_text. Best effort: a DB user without privileges logs a warning and the
# scan-based search still works. Set POSTGRES_FTS_INDEX=0 to skip index creation.
POSTGRES_FTS_INDEX: bool = os.environ.get("POSTGRES_FTS_INDEX", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def postgres_conninfo() -> str:
    """Return the PostgreSQL connection string, or ``""`` when not configured.

    Prefers ``POSTGRES_DSN``; otherwise assembles a libpq keyword string from the
    discrete ``POSTGRES_*`` variables. Never logs or returns credentials to callers
    other than the driver.
    """
    if POSTGRES_DSN:
        return POSTGRES_DSN
    if POSTGRES_DATABASE and POSTGRES_USER:
        user = quote(POSTGRES_USER, safe="")
        password = quote(POSTGRES_PASSWORD, safe="")
        database = quote(POSTGRES_DATABASE, safe="")
        return f"postgresql://{user}:{password}@{POSTGRES_HOST}:{POSTGRES_PORT}/{database}"
    return ""


# ---------------------------------------------------------------------------
# MySQL / MariaDB connection (optional sink)
# ---------------------------------------------------------------------------
MYSQL_HOST: str = os.environ.get("MYSQL_HOST", "")
MYSQL_PORT: str = os.environ.get("MYSQL_PORT", "3306")
MYSQL_DATABASE: str = os.environ.get("MYSQL_DATABASE", "")
MYSQL_USER: str = os.environ.get("MYSQL_USER", "")
MYSQL_PASSWORD: str = os.environ.get("MYSQL_PASSWORD", "")

MARIADB_HOST: str = os.environ.get("MARIADB_HOST", "")
MARIADB_PORT: str = os.environ.get("MARIADB_PORT", "")
MARIADB_DATABASE: str = os.environ.get("MARIADB_DATABASE", "")
MARIADB_USER: str = os.environ.get("MARIADB_USER", "")
MARIADB_PASSWORD: str = os.environ.get("MARIADB_PASSWORD", "")


def _parse_driver_dsn(dsn: str) -> Dict[str, object]:
    """Parse ``mysql://user:pass@host:port/db`` into PyMySQL connection kwargs."""
    parsed = urlparse(dsn)
    kwargs: Dict[str, object] = {}
    if parsed.hostname:
        kwargs["host"] = parsed.hostname
    if parsed.port:
        kwargs["port"] = int(parsed.port)
    if parsed.username:
        kwargs["user"] = unquote(parsed.username)
    if parsed.password:
        kwargs["password"] = unquote(parsed.password)
    database = (parsed.path or "").lstrip("/")
    if database:
        kwargs["database"] = database
    return kwargs


def mysql_conn_kwargs(prefix: str = "MYSQL") -> Dict[str, object]:
    """Return PyMySQL connection kwargs for *prefix*, or ``{}`` when unconfigured.

    ``MARIADB`` falls back to the ``MYSQL_*`` variables when its own are absent.
    """
    dsn = os.environ.get(f"{prefix}_DSN", "")
    if dsn:
        return _parse_driver_dsn(dsn)

    def value(name: str) -> str:
        own = os.environ.get(f"{prefix}_{name}", "")
        if own:
            return own
        if prefix != "MYSQL":
            return os.environ.get(f"MYSQL_{name}", "")
        return ""

    host = value("HOST")
    database = value("DATABASE")
    user = value("USER")
    if not (host and database and user):
        return {}
    kwargs: Dict[str, object] = {
        "host": host,
        "port": int(value("PORT") or "3306"),
        "database": database,
        "user": user,
    }
    password = value("PASSWORD")
    if password:
        kwargs["password"] = password
    return kwargs


# ---------------------------------------------------------------------------
# SQLite connection (optional sink)
# ---------------------------------------------------------------------------
# A filesystem path, or ":memory:" for an ephemeral database.
SQLITE_PATH: str = os.environ.get("SQLITE_PATH", "")

# ---------------------------------------------------------------------------
# DuckDB connection (optional sink)
# ---------------------------------------------------------------------------
DUCKDB_PATH: str = os.environ.get("DUCKDB_PATH", "")

# ---------------------------------------------------------------------------
# MongoDB connection (optional sink)
# ---------------------------------------------------------------------------
MONGODB_URI: str = os.environ.get("MONGODB_URI", "")
MONGODB_DATABASE: str = os.environ.get("MONGODB_DATABASE", "")

# ---------------------------------------------------------------------------
# ClickHouse connection (optional sink)
# ---------------------------------------------------------------------------
CLICKHOUSE_HOST: str = os.environ.get("CLICKHOUSE_HOST", "")
CLICKHOUSE_PORT: str = os.environ.get("CLICKHOUSE_PORT", "8123")
CLICKHOUSE_DATABASE: str = os.environ.get("CLICKHOUSE_DATABASE", "")
CLICKHOUSE_USER: str = os.environ.get("CLICKHOUSE_USER", "")
CLICKHOUSE_PASSWORD: str = os.environ.get("CLICKHOUSE_PASSWORD", "")

# ---------------------------------------------------------------------------
# Neo4j connection (optional sink)
# ---------------------------------------------------------------------------
NEO4J_URI: str = os.environ.get("NEO4J_URI", "")
NEO4J_USER: str = os.environ.get("NEO4J_USER", "")
NEO4J_PASSWORD: str = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE: str = os.environ.get("NEO4J_DATABASE", "")

# ---------------------------------------------------------------------------
# Graph linking (optional)
# ---------------------------------------------------------------------------
# Set COMPANY_TABLE to the name of your company table to enable graph edges.
# Leave empty to disable graph linking entirely.
COMPANY_TABLE: str = os.environ.get("COMPANY_TABLE", "")

# Pattern for converting a ticker like "0451.HK" into a record ID.
# Placeholders: {code} = leading-zero-stripped stock code, {exchange} = exchange suffix.
# Example: "{code}_{exchange}" produces "451_HK".
COMPANY_ID_PATTERN: str = os.environ.get("COMPANY_ID_PATTERN", "{code}_{exchange}")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LOG_DIR: Path = Path.cwd() / "logs"

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------
BATCH_SIZE: int = 100
MAX_DOWNLOAD_WORKERS: int = int(os.environ.get("MAX_DOWNLOAD_WORKERS", "15"))
MAX_DOWNLOAD_SIZE: int = 25 * 1024 * 1024  # 25 MB
MAX_SQL_BODY_SIZE: int = 900 * 1024  # ~900 KB text limit (SurrealDB /sql endpoint = 1 MiB)
MAX_RPC_BODY_SIZE: int = 3_800_000  # ~3.8 MB safe limit (SurrealDB /rpc endpoint = 4 MiB)

# ---------------------------------------------------------------------------
# Timeouts (see the 2026-10-25 backfill stalls)
# ---------------------------------------------------------------------------
#: Read timeout for one HKEX API page. 120 s multiplied by the retry policy
#: (~5 attempts + backoff) produced ~21-minute stalls on a slow month.
API_READ_TIMEOUT: int = int(os.environ.get("API_READ_TIMEOUT", "30"))
#: Hard ceiling for one date chunk. A chunk that exceeds it raises, and the run
#: logs the month as failed and moves on instead of hanging.
CHUNK_DEADLINE_SECONDS: int = int(os.environ.get("CHUNK_DEADLINE_SECONDS", "300"))
#: Hard ceiling for the post-ingest graph phase, which otherwise runs unbounded.
LINK_TIMEOUT_SECONDS: int = int(os.environ.get("LINK_TIMEOUT_SECONDS", "300"))

# ---------------------------------------------------------------------------
# Single-producer coordination
# ---------------------------------------------------------------------------
#: Session-level advisory lock key. Only the holder scrapes; other nodes stay idle,
#: so running the same unit on 01/02/03 cannot double-load HKEx or duplicate
#: coverage rows. Distinct from the tracker's DDL lock.
SCRAPE_LOCK_KEY: int = int(os.environ.get("SCRAPE_LOCK_KEY", "917231002"))
#: Set ``SCRAPER_LOCK=0`` for a one-off manual run that must not wait for the lock.
SCRAPE_LOCK_ENABLED: bool = os.environ.get("SCRAPER_LOCK", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}

#: Timestamp file written on every heartbeat. The watchdog reads this rather than the
#: database, because it deliberately depends on nothing but coreutils; set it to an
#: empty string to disable the file.
SCRAPER_HEARTBEAT_FILE: str = os.environ.get(
    "SCRAPER_HEARTBEAT_FILE", "/var/lib/hkex-scraper/heartbeat"
)

# ---------------------------------------------------------------------------
# HKEx API
# ---------------------------------------------------------------------------
HKEX_BASE_URL: str = "https://www1.hkexnews.hk"
HKEX_SEARCH_PAGE: str = f"{HKEX_BASE_URL}/search/titlesearch.xhtml"
HKEX_API_ENDPOINT: str = f"{HKEX_BASE_URL}/search/titleSearchServlet.do"
