---
description: "Every environment variable for HKEx Filing Scraper, including the DATABASE_TARGET sink selector and per-sink connection settings."
---

# Configuration Reference

Configuration is read from environment variables. A `.env` file in the **current working directory** is loaded automatically (requires `python-dotenv`; if it is missing, environment variables still work). `.env` is never committed.

## Database sink selection

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `DATABASE_TARGET` | **required** | Comma-separated, ordered list of sink ids. Valid: `postgres`, `mysql`, `sqlite`, `mongodb`, `mariadb`, `neo4j`, `clickhouse`, `duckdb`, `surrealdb`. Example: `postgres,sqlite`. There is no default; an unset or unknown value fails fast. Reads are served by the first configured sink that supports them. |

Every configured sink is **required**: if any sink's write fails, the run exits non-zero.

See [docs/sinks/README.md](sinks/README.md) for the support matrix.

## PostgreSQL

Required when `DATABASE_TARGET` includes `postgres`.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `POSTGRES_DSN` | — | Full DSN, e.g. `postgresql://user:pass@host:5432/db`. Takes precedence over discrete settings. |
| `POSTGRES_HOST` | `localhost` | Host. |
| `POSTGRES_PORT` | `5432` | Port. |
| `POSTGRES_DATABASE` | — | Database name. |
| `POSTGRES_USER` | — | User. |
| `POSTGRES_PASSWORD` | — | Password (URL-encoded automatically when assembled from discrete settings). |
| `POSTGRES_SCHEMA` | `public` | Schema for the mirrored tables. |
| `POSTGRES_MIN_POOL` | `1` | Minimum pool connections. |
| `POSTGRES_MAX_POOL` | `15` | Maximum pool connections. |
| `POSTGRES_FTS_INDEX` | `1` | Create the optional `pg_trgm` GIN indexes that accelerate substring search on `title` and `document_text`. Best effort — a user without privilege logs a warning and search falls back to a scan. Set to `0` to skip index creation. |

## MySQL / MariaDB

Required when `DATABASE_TARGET` includes `mysql` (or `mariadb`). Install the driver with
`pip install ".[mysql]"`.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `MYSQL_DSN` | — | Full DSN, e.g. `mysql://user:pass@host:3306/db`. Takes precedence over discrete settings. |
| `MYSQL_HOST` | — | Host. |
| `MYSQL_PORT` | `3306` | Port. |
| `MYSQL_DATABASE` | — | Database name. |
| `MYSQL_USER` | — | User. |
| `MYSQL_PASSWORD` | — | Password. |
| `MARIADB_DSN` / `MARIADB_HOST` / `MARIADB_PORT` / `MARIADB_DATABASE` / `MARIADB_USER` / `MARIADB_PASSWORD` | — | The `mariadb` sink uses these and falls back to the matching `MYSQL_*` variable when unset. |

## SQLite

Required when `DATABASE_TARGET` includes `sqlite`. No extra dependency (stdlib `sqlite3`).

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `SQLITE_PATH` | — | Filesystem path to the database file, or `:memory:` for an ephemeral database. |

## MongoDB

Required when `DATABASE_TARGET` includes `mongodb`. Install the driver with
`pip install ".[mongodb]"`.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `MONGODB_URI` | — | Connection URI, e.g. `mongodb://user:pass@host:27017/?authSource=admin`. |
| `MONGODB_DATABASE` | — | Database name. |

## Neo4j

Required when `DATABASE_TARGET` includes `neo4j`. Install the driver with
`pip install ".[neo4j]"`.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `NEO4J_URI` | — | Bolt URI, e.g. `bolt://localhost:7687`. |
| `NEO4J_USER` | — | User. |
| `NEO4J_PASSWORD` | — | Password. |
| `NEO4J_DATABASE` | — | Database name; omit for the server default. |

## ClickHouse

Required when `DATABASE_TARGET` includes `clickhouse`. Install the driver with
`pip install ".[clickhouse]"`.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `CLICKHOUSE_HOST` | — | Host. |
| `CLICKHOUSE_PORT` | `8123` | HTTP port. |
| `CLICKHOUSE_DATABASE` | — | Database name (must exist). |
| `CLICKHOUSE_USER` | `default` | User. |
| `CLICKHOUSE_PASSWORD` | — | Password. |

## DuckDB

Required when `DATABASE_TARGET` includes `duckdb`. Install the driver with
`pip install ".[duckdb]"`.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `DUCKDB_PATH` | — | Filesystem path to the database file, or `:memory:`. |

## SurrealDB

Required when `DATABASE_TARGET` includes `surrealdb`.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `SURREAL_ENDPOINT` | — | HTTP endpoint, e.g. `http://localhost:8000`. |
| `SURREAL_NAMESPACE` | `default` | Namespace. |
| `SURREAL_DATABASE` | `default` | Database. |
| `SURREAL_USERNAME` | `root` | Username. |
| `SURREAL_PASSWORD` | — | Password. |

## Graph linking

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `COMPANY_TABLE` | — | Company table name. When empty, graph linking is disabled. |
| `COMPANY_ID_PATTERN` | `{code}_{exchange}` | Pattern that converts a ticker (`0451.HK`) into a company key (`451_HK`). |

## MCP server

The optional [MCP server](mcp.md) introduces no new environment variables: it reuses the
same `DATABASE_TARGET` and sink connection settings and reads them from `Path.cwd()/.env`.
Because the database adapters do not enforce read-only access, point the MCP server at a
database user that only has `SELECT` (or a read replica). See [MCP server](mcp.md#safety).

## Performance

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `MAX_DOWNLOAD_WORKERS` | `15` | Parallel document downloads. |
| `REQUEST_DELAY_SECONDS` | `0` | Minimum seconds between request starts, process-wide. `0` disables pacing; set a small value (e.g. `0.25`) to be deliberately polite to the endpoint. |

## Precedence

1. `--database-target` command-line override (highest).
2. Environment variables / `.env`.
3. No implicit sink default — `DATABASE_TARGET` must be set.

## Constants (not configurable)

| Constant | Value | Meaning |
| -------- | ----- | ------- |
| `MAX_DOWNLOAD_SIZE` | 25 MB | Documents larger than this are skipped (`too_large`). |
| `MAX_SQL_BODY_SIZE` | ~900 KB | SurrealDB `/sql` body limit. |
| `MAX_RPC_BODY_SIZE` | ~3.8 MB | SurrealDB `/rpc` body limit. |
| `BATCH_SIZE` | 100 | Metadata batch size. |
