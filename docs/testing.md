# Testing

## Unit tests (no database, no network)

```bash
pip install -e ".[dev]"
pytest -q                 # all tests; integration tests skip automatically
ruff check && ruff format --check
```

The default run needs neither a database nor the network.

## In-process sinks (SQLite, DuckDB)

SQLite uses the standard-library `sqlite3` module and DuckDB runs in-process, so both contract
suites execute on every `pytest` run when the driver is installed:

```bash
pytest -q tests/test_sqlite_integration.py tests/test_duckdb_integration.py
```

To exercise them through the multi-sink dispatch path:

```bash
export DATABASE_TARGET=postgres,sqlite
export POSTGRES_DSN=postgresql://hkex:hkex@localhost:5432/hkex
export SQLITE_PATH=/tmp/hkex-it.db
pytest -q
```

## Server-backed sinks

These suites are skipped unless the matching connection variables are set. Each needs its
driver installed.

### PostgreSQL

```bash
export DATABASE_TARGET=postgres
export POSTGRES_DSN=postgresql://hkex:hkex@localhost:5432/hkex
pip install -e ".[dev,postgres]"
pytest -q tests/test_postgres_integration.py
```

### MySQL / MariaDB

```bash
export DATABASE_TARGET=mysql
export MYSQL_HOST=localhost MYSQL_DATABASE=hkex MYSQL_USER=hkex MYSQL_PASSWORD=hkex
pip install -e ".[dev,mysql]"
pytest -q tests/test_mysql_integration.py
```

### SurrealDB

```bash
export DATABASE_TARGET=surrealdb
export SURREAL_ENDPOINT=http://localhost:8000
export SURREAL_NAMESPACE=test
export SURREAL_DATABASE=hkex
export SURREAL_USERNAME=root
export SURREAL_PASSWORD=root
export COMPANY_TABLE=company        # enables the graph-edge test
pytest -q tests/test_surrealdb_integration.py
```

A throwaway SurrealDB (in-memory):

```bash
docker run -d --name hkex-surreal -p 8000:8000 \
  surrealdb/surrealdb:v3.2.4 start --log warn --user root --pass root --bind 0.0.0.0:8000 memory
```

### Sinks (MongoDB, ClickHouse, Neo4j)

```bash
export DATABASE_TARGET=mongodb,clickhouse,neo4j
export MONGODB_URI=mongodb://localhost:27017
export MONGODB_DATABASE=hkex
export CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8123 CLICKHOUSE_DATABASE=hkex
export CLICKHOUSE_USER=hkex CLICKHOUSE_PASSWORD=hkex
export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=hkexpassword
pip install -e ".[dev,duckdb,mongodb,clickhouse,neo4j]"
pytest -q tests/test_extra_sinks_integration.py
```

## Multi-sink integration

```bash
export DATABASE_TARGET=postgres,surrealdb,sqlite
export SURREAL_ENDPOINT=http://localhost:8000
export SURREAL_NAMESPACE=test
export SURREAL_DATABASE=hkex
export SURREAL_USERNAME=root
export SURREAL_PASSWORD=root
export POSTGRES_DSN=postgresql://hkex:hkex@localhost:5432/hkex
export SQLITE_PATH=/tmp/hkex-it.db
pytest -q tests/test_postgres_integration.py tests/test_surrealdb_integration.py tests/test_dual_write_integration.py
```

Every configured sink must have its schema initialized; the integration fixtures do this for
the whole configured set (matching `main._init_schemas()`).

## What is covered

| Test file | Requires | Checks |
| --------- | -------- | ------ |
| `tests/test_utils.py` | nothing | helpers, classification, ticker extraction, company-id loading (incl. missing table) |
| `tests/test_coverage.py` | nothing | coverage statement/logging shape |
| `tests/test_config.py` | nothing | `DATABASE_TARGET` parsing, CLI override, MySQL/PostgreSQL connection helpers |
| `tests/test_registry.py` | nothing | lazy driver loading, valid ids, capability declarations, read routing |
| `tests/test_dialects.py` | nothing | per-dialect DDL, upsert/edge/coverage SQL, value encoding |
| `tests/test_relational_sink.py` | nothing | driver-missing/unconfigured degradation, credential redaction |
| `tests/test_postgres_sink.py` | nothing | PostgreSQL DDL, upserts, degradation, redaction, adapter |
| `tests/test_sink_dispatch.py` | nothing | multi-sink dispatch, failure isolation and counters, graph dispatch, parity report |
| `tests/test_docs_consistency.py` | nothing | every sink id appears in the docs, templates, and `.env.example`; `DATABASE_TARGET` rows list every id |
| `tests/test_sqlite_integration.py` | nothing | full SQLite contract: schema, idempotent upserts, no-clobber, edges, reads |
| `tests/test_extra_sinks.py` | nothing | MongoDB/ClickHouse/Neo4j capabilities, degradation, ClickHouse merge, Neo4j Cypher |
| `tests/test_duckdb_integration.py` | `duckdb` installed | full DuckDB contract in-process |
| `tests/test_extra_sinks_integration.py` | `MONGODB_*` / `CLICKHOUSE_*` / `NEO4J_*` | live contract per document/columnar/graph engine |
| `tests/test_mysql_integration.py` | `MYSQL_*` or `MARIADB_*` | live MySQL/MariaDB contract, incl. `mariadb` fallback to `MYSQL_*` |
| `tests/test_postgres_integration.py` | `POSTGRES_DSN` | schema, idempotent upserts, JSONB/array round-trip, no-clobber, coverage, edge counts |
| `tests/test_surrealdb_integration.py` | `SURREAL_*` | schema, idempotent upserts, document payload, coverage, graph edges |
| `tests/test_dual_write_integration.py` | two or more sinks | the same filing lands in every configured sink; document status mirrors |

## Fixtures, fault injection, and the canary

- **Replayed API contract** (`tests/test_api_contract.py`): `responses` serves the recorded
  response shapes (JSF page, form POST, JSON pages), so the full fetch path — ViewState
  extraction, pagination, tolerant parsing of nulls/non-strings/CJK titles — is exercised
  offline. Regressions here mean the HKEx API changed shape.
- **Fault injection** (`tests/test_fault_injection.py`): scripted sinks prove that one sink
  failing (or raising) never blocks another, that failures are counted, and that the run exits
  non-zero.
- **Canary** (`scripts/canary.py`, `.github/workflows/canary.yml`): a daily live check that
  fetches a small recent window and asserts the shape is unchanged, opening an issue when it is
  not. Run it by hand with `python scripts/canary.py --days 7 --max 5 --json`.

## CI

`.github/workflows/ci.yml` runs lint/format, a markdown lint, unit tests on Python 3.10–3.13
(with DuckDB, so its in-process contract tests run), and integration jobs: PostgreSQL (service
container), MySQL (service container), SurrealDB (`docker run`), multi-sink (PostgreSQL +
SurrealDB), and a job covering MongoDB, ClickHouse, and Neo4j service containers. The SurrealDB image
is pinned to `v3.2.4`.

## See also

- [Contributing](https://github.com/simonmak-ascent/hkex-filing-scraper/blob/main/CONTRIBUTING.md)
- [De-risking register](de-risking.md)
