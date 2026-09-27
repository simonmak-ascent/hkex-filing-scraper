# Constitution

Status: Approved
Version: 4.0
Last updated: 2026-09-16

> Impact Chain: Phase 0 — Constitution (immutable)

## Purpose

Build the most useful, feature-rich, and trustworthy open-source tool in the HKEx regulatory filings data category. The tool scrapes HKEx regulatory filings via an undocumented JSON API and ingests them into **one or more configured database sinks** — `postgres`, `mysql`, `sqlite`, `mongodb`, `mariadb`, `neo4j`, `clickhouse`, `duckdb`, `surrealdb` — with full-text/tables extraction and optional graph edges, so downstream systems can process a complete, faithful, provenance-preserving corpus from whichever store they already operate. All sinks are first-class; none is privileged.

## Core Principles

### 1. Completeness Over Convenience

Every filing from April 1999 onward must be reachable. Gaps are a bug. Delisted issuers, amendments, attachments, Chinese-language filings, and non-PDF documents (Excel, HTML) are all first-class citizens. Never silently drop records.

### 2. Data Fidelity Over Speed

Extracted text and tables must faithfully represent the source document. Merged cells, reading order, bilingual content, and page structure matter. When fidelity is impossible (scanned PDFs, corrupted files), the tool MUST report extraction status and confidence — never silently produce garbage.

### 3. Reliability Through Resilience

The pipeline must survive network failures, rate limits, endpoint changes, and database errors. Batching, retries with exponential backoff, checksums, idempotent upserts, and stalled-job detection are not optional — they are the baseline.

### 4. Provenance is Non-Negotiable

Every extracted fact, table cell, and graph edge must trace back to the exact filing, document version, and page. No information without origin. This applies equally to AI-generated summaries and raw text extraction, and to every sink the data is written to.

### 5. Developer-First Delivery

The tool must be installable with one command, runnable from CLI, queryable via standard interfaces, and extensible without modifying core. SDKs, MCP tools, and API wrappers are natural extensions — not afterthoughts.

### 6. Observed Behavior Over Assumed Behavior

Configuration is loaded from CWD `.env` — not the project root. SurrealDB `/rpc` parses `word:word` as record links. The HKEx API requires a JSF ViewState round-trip. PostgreSQL `ON CONFLICT` is the idempotency mechanism. These quirks are documented in AGENTS.md, not buried in source comments.

### 7. Schema Stability Must Be Explicit

Every sink has an explicit schema. `exchange_filing` is SCHEMAFULL in SurrealDB and a typed `CREATE TABLE IF NOT EXISTS` model in PostgreSQL; the other sinks mirror that model in their own dialect. Adding a field requires updating `db.py:_build_schema_sql()`, `db_postgres.py:_build_postgres_schema_sql()`, `sinks/dialects.py`, and the document/graph adapter field lists. Migrations run safely at every startup using `IF NOT EXISTS`.

### 8. Progressive Enhancement Over Monolithic Dependencies

Core scraping works with only `requests` + `beautifulsoup4`. PDF extraction (PyMuPDF, pymupdf4llm, camelot-py), Excel parsing (openpyxl), env loading (python-dotenv), and the PostgreSQL driver (`psycopg[binary,pool]`) are optional extras. The tool must function gracefully when optional deps are missing.

### 9. Sink Parity and Explicit Failure

When more than one sink is configured, every record written to one sink MUST be attempted on the other. A failure on any configured sink is logged, counted, and surfaced — never swallowed. The pipeline MUST NOT report success for a record whose configured sink writes did not all succeed.

## Technology Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python 3.10+ | Type hints throughout; `from __future__ import annotations` |
| Build | hatchling | src-layout, single console script `hkex-scraper` |
| HTTP | `requests` | base dep, always available |
| HTML parsing | `beautifulsoup4` | base dep, always available |
| Optional PDF | PyMuPDF, pymupdf4llm (AGPL-3.0), camelot-py | guarded by `_AVAILABLE` flags; the `pdf` extra is excluded from `all` |
| Optional Excel | openpyxl | guarded by `_AVAILABLE` flags |
| Optional env | python-dotenv | graceful fallback if missing |
| Database sinks | `postgres`, `mysql`, `sqlite`, `mongodb`, `mariadb`, `neo4j`, `clickhouse`, `duckdb`, `surrealdb` | selected via ordered `DATABASE_TARGET`; contract in `sinks/base.py:Sink`, registry in `sinks/registry.py`; order is documented popularity order |
| Relational PostgreSQL | PostgreSQL 13+ | `psycopg` 3.x (`psycopg[binary,pool]`), `ON CONFLICT` upserts, JSONB for tables |
| Relational MySQL/MariaDB | MySQL 8 / MariaDB 10.5+ | `PyMySQL` (`mysql` extra), `ON DUPLICATE KEY UPDATE` upserts |
| Relational SQLite / DuckDB | SQLite 3 / DuckDB 1.x | stdlib `sqlite3`; `duckdb` extra; `ON CONFLICT` upserts, JSON columns |
| Document MongoDB | MongoDB 6+ | `pymongo` (`mongodb` extra), `$set` upserts on `_id` |
| Columnar ClickHouse | ClickHouse 24+ | `clickhouse-connect` (`clickhouse` extra), `ReplacingMergeTree` |
| Graph Neo4j | Neo4j 5 (Community) | `neo4j` driver (`neo4j` extra), `MERGE` nodes/relationships |
| Graph/document SurrealDB | SurrealDB | `/sql` (1 MiB) + `/rpc` (4 MiB) endpoints |
| Testing | pytest | pure unit tests, no DB/network |
| Lint | ruff | py310 target, line-length 100 |

## Domain Primitives

| Domain Term | Definition |
|-------------|-----------|
| Filing | A single HKEx regulatory disclosure (metadata + one source document) |
| Phase 1 | Scrape filing metadata from the HKEx JSON API |
| Phase 2 | Download documents + extract text/tables into Markdown |
| Chunk | One month-range slice of the search space when no stock code is specified |
| `filingId` | MD5 hash of key fields used for deduplication (primary key in every sink) |
| `documentStatus` | `processed` / `skipped` / `failed` outcome of document processing |
| Graph edge | `has_filing` (company → filing) or `references_filing` (title mention) |
| Sink | A configured persistence target — one of `postgres`, `mysql`, `sqlite`, `mongodb`, `mariadb`, `neo4j`, `clickhouse`, `duckdb`, `surrealdb` (`DATABASE_TARGET`, an ordered CSV list) |
| Multi-write | Writing the same record to every configured sink within one pipeline phase |

## Security Constraints

1. No hardcoded credentials — all secrets come from environment variables or `.env`.
2. `.env` is loaded from `Path.cwd()`, never the project root; never commit `.env`.
3. Database auth (SurrealDB NS/DB/user/pass, PostgreSQL DSN) is built at runtime from config, never logged.
4. SQL string escaping via `utils.escape_sql()` is mandatory for all SurrealDB `/sql` paths to prevent injection.
5. SurrealDB `/rpc` is preferred for parameterized saves to avoid record-link and injection issues.
6. PostgreSQL statements MUST use bound parameters (`%s` / `%(name)s`) — never string-concatenate values into SQL.
7. Log files (`logs/`) and failed-SQL dumps (`logs/hkex_failed.sql`) must not contain credentials; redact before logging.
8. No execution of remote content — extracted document text is treated as untrusted data, never evaluated.

## Banned Patterns

- Plain `.js` / non-Python source in the package.
- Synchronous blocking HTTP inside the Phase 2 worker pool.
- Silent `except:` clauses — every failure path must log via `utils.log()`.
- Direct `CREATE TABLE` without `IF NOT EXISTS` in schema DDL (SurrealDB or PostgreSQL).
- `documentContent` blob field (obsolete — auto-removed on startup).
- Copy-pasting record-link-interpretation-prone strings into SurrealDB `/rpc` without the `/sql` fallback check.
- Embedding unparameterised values into PostgreSQL SQL.
- Importing `psycopg` at module top level without an `_AVAILABLE` guard.

## File Structure

```text
src/hkex_scraper/
  main.py        # CLI parsing, orchestration, validation
  config.py      # env vars + constants (loaded at import from CWD .env)
  api.py         # HKEx JSON API session, chunking, record parsing
  pipeline.py    # Phase 1 metadata save, Phase 2 download/extract/save loop
  extractor.py   # PDF/HTML/Excel text + table extraction → Markdown
  db.py          # SurrealDB /sql + /rpc helpers, schema DDL, batch upsert
  db_postgres.py # PostgreSQL sink: optional driver, DDL, parameterized ON CONFLICT upserts
  sinks/         # Sink contract, registry, SQL dialects, relational engine, per-engine adapters
  graph.py       # has_filing / references_filing edge dispatch
  utils.py       # logging, string helpers, filing classification, tickers
tests/           # pure unit tests (no DB/network)
```

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Uniform sink contract with a lazy registry | One `Sink` interface + declared capabilities + hand-written dialects; adding a sink is one adapter + one registry entry, not a four-file edit |
| Relational sinks for SQL teams | PostgreSQL, MySQL/MariaDB, SQLite let downstream teams use tooling they already run |
| SurrealDB as the graph/document sink | Native graph edges, SCHEMAFULL integrity, parameterized RPC for large payloads |
| Sink selection via `DATABASE_TARGET` | An ordered CSV list of sink ids; order sets read precedence (first read-capable sink). No silent default — unset or unknown fails fast |
| Open-source-first support policy | OSI-approved engines by default; source-available engines (SurrealDB, and later MongoDB) are explicit, labeled exceptions (see `docs/adr/0003-sink-support-policy.md`) |
| Two-phase pipeline (metadata → documents) | Decouples fast metadata ingestion from slow document processing; enables backfill and incremental updates |
| CLI-first, not GUI-first | Maximizes composability with scripts, cron jobs, and CI/CD pipelines |
| Undocumented JSON API over browser automation | Faster, more reliable batch scraping; no Selenium dependency |
| SurrealDB `/rpc` first with `/sql` fallback | 4x larger body limit for parameterized queries; escape to `/sql` only when record-link bug strikes |
| `filingId` as the shared dedup key across sinks | Same MD5-16 identifier gives cross-sink idempotency and a join key for parity checks |

## Prohibitions

- No production dependency on prohibited scraping (HKEX Terms of Use §4.3). The undocumented API is a research tool. Commercial redistribution requires licensed feed access.
- No silent data loss. Every truncation, skip, or failure must be logged and surfaced via `documentStatus` and `documentStatusReason`, and via per-sink write counters.
- No breaking schema changes without migration. Every new field must have a `DEFINE FIELD IF NOT EXISTS` (SurrealDB) and a matched `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (PostgreSQL), plus a matching `sinks/dialects.py` column.
- No hardcoded credentials. All secrets come from environment variables or `.env`.
- No base dependency on any database driver. All drivers are optional extras with graceful degradation; a configured but unusable sink fails fast with an actionable message rather than appearing to succeed.
