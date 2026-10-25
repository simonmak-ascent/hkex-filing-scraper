---
description: Fixes for the errors you are most likely to hit: missing DATABASE_TARGET, driver imports, connection failures, empty results, and document extraction.
---

# Troubleshooting

## Configuration

**`ERROR: DATABASE_TARGET is not set. Set it to one or more sink ids (e.g. DATABASE_TARGET=postgres,sqlite)`**
There is no implicit default. Set `DATABASE_TARGET` to a comma-separated list of ids
(`postgres`, `mysql`, `sqlite`, `mongodb`, `mariadb`, `neo4j`, `clickhouse`, `duckdb`,
`surrealdb`).

**`ERROR: unknown sink 'x'; valid sinks are: ...`**
A sink id is misspelled. The message lists the valid ids in documented order.

**`ERROR: <sink> sink requires ...` then `ERROR: refusing to start with an unusable configured sink.`**
Every configured sink must be usable. Each message names the missing driver extra or setting,
for example:

- `postgres` — `pip install ".[postgres]"` and/or set `POSTGRES_DSN` / `POSTGRES_*`.
- `mysql`, `mariadb` — `pip install ".[mysql]"` and/or set `MYSQL_*` (or `MARIADB_*` / `*_DSN`).
- `sqlite` — set `SQLITE_PATH` (a file path, or `:memory:`).
- `mongodb` — `pip install ".[mongodb]"` and set `MONGODB_URI` / `MONGODB_DATABASE`.
- `neo4j` — `pip install ".[neo4j]"` and set `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`.
- `clickhouse` — `pip install ".[clickhouse]"` and set `CLICKHOUSE_*`.
- `duckdb` — `pip install ".[duckdb]"` and set `DUCKDB_PATH`.
- `surrealdb` — set `SURREAL_ENDPOINT` / `SURREAL_PASSWORD`.

**`.env` is not being read**
`.env` is loaded from the **current working directory**, not the repository root. Run the CLI
from the directory that contains `.env`, or export the variables.

## PostgreSQL

**`PostgreSQL sink requires POSTGRES_DSN or POSTGRES_DATABASE/POSTGRES_USER connection details`**
Set `POSTGRES_DSN` (preferred) or `POSTGRES_DATABASE` + `POSTGRES_USER` (+ `POSTGRES_PASSWORD`).

**`POSTGRES_WRITE_ERROR` on document save**
The `UPDATE` matched no row — the filing metadata was not written first. Run a metadata pass
(`--metadata-only`) or a full run so the row exists.

**Tables are not created**
Schema creation runs on startup for every configured sink. Check the log line
`postgres schema initialized successfully`. A failure is fatal (the run exits `1`).

## MySQL / MariaDB

**`MySQL sink requires PyMySQL (install with: pip install ".[mysql]")`**
Install the driver extra.

**`MySQL sink requires MYSQL_HOST/MYSQL_DATABASE/MYSQL_USER (or MYSQL_DSN)`**
Set the connection settings; the `mariadb` sink falls back to `MYSQL_*` when `MARIADB_*` is
unset.

**`Access denied` / `Unknown database`**
Check credentials, or create the database: `CREATE DATABASE hkex CHARACTER SET utf8mb4;`.

## SQLite

**`SQLite sink requires SQLITE_PATH`**
Set `SQLITE_PATH` to a writable file path, or `:memory:`.

**`database is locked`**
SQLite serialises writers, so the sink enables WAL mode and a 30 s busy timeout. Two
*processes* writing the same file can still contend — point each run at its own file, or use a
server-backed sink for concurrent writers.

## SurrealDB

**`/rpc` record-link error / `expected a option<...>`**
SurrealDB interprets strings shaped like `word:word` as record links. The scraper detects this
and retries via `/sql` automatically.

**Document saved with fewer tables than expected**
The `/rpc` (≈3.8 MB) or `/sql` (≈1 MB) body limits were hit and the payload was truncated.
This is logged and recorded in `documentStatusReason` (e.g. `truncated_from_...`).

## Multi-sink

**`WARNING: sinks differ by N record(s)` from `--parity-report`**
Sinks differ in filing count. Common causes: enabling a sink after a single-sink history (no
backfill — multi-write is forward-only), or a failed write on one sink (see the per-sink
failure counts).

**Graph edges missing**
Edge creation requires `COMPANY_TABLE`. The SurrealDB adapter creates an edge only for tickers
matched in the company table; the other edge-capable sinks create edges for every ticker.

## Scraping

**Coverage gap (ingested < API total)**
Re-run the same range; deduplication means only missing filings are added. `--coverage-report`
shows per-chunk counts.

**`http_429` / repeated `http_5xx` skips**
The scraper already retries transient statuses (408/429/5xx) four times with exponential
backoff and honours `Retry-After`. If they persist, lower `MAX_DOWNLOAD_WORKERS` and set
`REQUEST_DELAY_SECONDS` (for example `0.5`) to slow the request rate, then re-run — dedup means
only the missing filings are fetched.

**Downloads skipped**
Documents over 25 MB, unsupported types, or HTTP errors are recorded as `skipped`/`failed`
with a reason such as `too_large`, `unsupported_type`, or `http_404`.

**No documents processed**
Phase 2 only processes filings with a `documentUrl` and no `documentStatus` in the **read
source** (the first configured sink that supports reads). Use `--metadata-only` first, then
`--backfill-docs`.

## Tests

**Integration tests are skipped**
Server-backed integration tests require `DATABASE_TARGET` to include the sink and its
connection variables to be set; without them they skip by design. SQLite and DuckDB tests
always run in-process.

**`ruff check` fails after upgrading ruff**
Ruff ≥ 0.16 enables many more rules by default. This project pins an explicit `select` and a
ruff version range in `pyproject.toml`; install the pinned version
(`pip install -e ".[dev]"`).
