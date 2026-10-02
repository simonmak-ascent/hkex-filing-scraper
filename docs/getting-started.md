---
description: Install HKEx Filing Scraper, set DATABASE_TARGET, and run your first HKEx filing scrape in minutes.
---

# Getting Started

## Requirements

- Python 3.10+.
- At least one supported sink. All nine are first-class destinations:

  | Sink | Needs |
  | ---- | ----- |
  | `postgres` | a PostgreSQL 13+ server |
  | `mysql` / `mariadb` | a MySQL 8+ or MariaDB 10.5+ server |
  | `sqlite` | nothing (stdlib) — a file or `:memory:` |
  | `mongodb` | a MongoDB 6+ server |
  | `neo4j` | a Neo4j 5 server |
  | `clickhouse` | a ClickHouse 24+ server |
  | `duckdb` | nothing (in-process) — a file or `:memory:` |
  | `surrealdb` | a SurrealDB server |

- A `.env` file in the **current working directory** (the scraper loads `Path.cwd()/.env`,
  not the repo root).

See [Database sinks](sinks/README.md) for the support matrix, licenses, and capability
differences.

See [Database sinks](sinks/README.md) for the support matrix, licenses, and capability differences.

## Install

```bash
pip install hkex-filing-scraper              # core; SQLite works out of the box
pip install "hkex-filing-scraper[all]"       # Excel + dotenv + every database driver + the MCP server
```

Minimal and per-sink installs:

```bash
pip install "hkex-filing-scraper[postgres]"  # add one driver at a time
pip install "hkex-filing-scraper[duckdb]"    # or: mysql, mongodb, clickhouse, neo4j
```

To run the latest unreleased code:

```bash
pip install "git+https://github.com/simonplmak-cloud/hkex-filing-scraper.git"
```

> The `pdf` extra installs **AGPL-3.0** libraries (PyMuPDF, pymupdf4llm). It is **not** part
> of `.[all]`. See [Legal & Terms of Use](legal.md#third-party-licenses) before installing it.

## Configure

```bash
cp .env.example .env
```

Set `DATABASE_TARGET` to an ordered, comma-separated list of sink ids, then add that sink's
connection settings. Order matters: reads come from the first read-capable sink in the list.

```ini
# PostgreSQL
DATABASE_TARGET=postgres
POSTGRES_DSN=postgresql://user:password@localhost:5432/hkex
# or: POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DATABASE / POSTGRES_USER / POSTGRES_PASSWORD

# MySQL / MariaDB
DATABASE_TARGET=mysql            # or mariadb
MYSQL_HOST=localhost  MYSQL_DATABASE=hkex  MYSQL_USER=hkex  MYSQL_PASSWORD=secret

# SQLite (no server)
DATABASE_TARGET=sqlite
SQLITE_PATH=hkex.db

# MongoDB
DATABASE_TARGET=mongodb
MONGODB_URI=mongodb://localhost:27017  MONGODB_DATABASE=hkex

# Neo4j
DATABASE_TARGET=neo4j
NEO4J_URI=bolt://localhost:7687  NEO4J_USER=neo4j  NEO4J_PASSWORD=secret

# ClickHouse
DATABASE_TARGET=clickhouse
CLICKHOUSE_HOST=localhost  CLICKHOUSE_DATABASE=hkex  CLICKHOUSE_USER=default

# DuckDB (no server)
DATABASE_TARGET=duckdb
DUCKDB_PATH=hkex.duckdb

# SurrealDB
DATABASE_TARGET=surrealdb
SURREAL_ENDPOINT=http://localhost:8000
SURREAL_NAMESPACE=default  SURREAL_DATABASE=default
SURREAL_USERNAME=root  SURREAL_PASSWORD=your_password
```

### Multiple sinks

```ini
# Order matters: reads come from the first read-capable sink.
DATABASE_TARGET=postgres,sqlite
POSTGRES_DSN=postgresql://user:password@localhost:5432/hkex
SQLITE_PATH=hkex.db
```

### Other backends

```ini
# MySQL / MariaDB
DATABASE_TARGET=mysql
MYSQL_HOST=localhost  MYSQL_DATABASE=hkex  MYSQL_USER=hkex  MYSQL_PASSWORD=secret

# DuckDB (no server)
DATABASE_TARGET=duckdb
DUCKDB_PATH=hkex.duckdb

# MongoDB
DATABASE_TARGET=mongodb
MONGODB_URI=mongodb://localhost:27017  MONGODB_DATABASE=hkex

# ClickHouse
DATABASE_TARGET=clickhouse
CLICKHOUSE_HOST=localhost  CLICKHOUSE_DATABASE=hkex  CLICKHOUSE_USER=default

# Neo4j
DATABASE_TARGET=neo4j
NEO4J_URI=bolt://localhost:7687  NEO4J_USER=neo4j  NEO4J_PASSWORD=secret
```

`DATABASE_TARGET` is required — there is no implicit default. See
[Database sinks](sinks/README.md) for the full support matrix.

## First run

```bash
# Metadata only — fastest way to validate configuration
hkex-scraper --metadata-only --limit 100

# Full history (metadata + documents + graph)
hkex-scraper --full-history
```

The schema for every configured sink is created automatically on startup using idempotent DDL.

## Verify

```bash
hkex-scraper --coverage-report                                  # chunk-level coverage
hkex-scraper --database-target postgres,sqlite --parity-report  # per-sink filing counts
```

Exit code is non-zero if **any** configured sink recorded write failures.

## Next steps

- [Database sinks (support matrix)](sinks/README.md)
- [Per-sink guides](sinks/README.md#per-sink-guides)
- [Configuration reference](configuration.md)
- [CLI reference](cli.md)
- [Troubleshooting](troubleshooting.md)
