---
description: Support matrix for every HKEx Filing Scraper sink — licenses, capability differences, and per-engine notes.
---

# Database sinks

The scraper writes each record to **every** sink listed in `DATABASE_TARGET` (a
comma-separated, ordered list of sink ids). Reads are served by the first configured sink
that supports them. Every configured sink is required: a write failure anywhere marks the
run non-zero.

```bash
# one sink
DATABASE_TARGET=postgres

# several (order sets read precedence)
DATABASE_TARGET=postgres,sqlite

# a filesystem database — no server required
DATABASE_TARGET=sqlite
SQLITE_PATH=hkex.db
```

## Support matrix

Rows are in the documented popularity order (see
[`sinks/registry.py`](https://github.com/simonmak-ascent/hkex-filing-scraper/blob/main/src/hkex_scraper/sinks/registry.py)). Every sink is a first-class
destination; none is privileged.

| Sink | Model | License | OSI | Extra | Idempotent upsert | Reads | Edges |
| ---- | ----- | ------- | --- | ----- | ----------------- | ----- | ----- |
| `postgres` | relational | PostgreSQL License | Yes | `postgres` | `ON CONFLICT DO UPDATE` | Yes | Yes |
| `mysql` | relational | GPLv2 (Community) | Yes | `mysql` | `ON DUPLICATE KEY UPDATE` | Yes | Yes |
| `sqlite` | relational | Public domain | Yes | — | `ON CONFLICT DO UPDATE` | Yes | Yes |
| `mongodb` | document | SSPL | No | `mongodb` | `update_one(upsert=True)` | Yes | Yes |
| `mariadb` | relational | GPLv2 | Yes | `mysql` | `ON DUPLICATE KEY UPDATE` | Yes | Yes |
| `neo4j` | graph | GPLv3 (Community) | Yes | `neo4j` | `MERGE` | Yes | Yes |
| `clickhouse` | columnar | Apache-2.0 | Yes | `clickhouse` | `ReplacingMergeTree` + read-merge | Yes | Yes |
| `duckdb` | relational | MIT | Yes | `duckdb` | `ON CONFLICT DO UPDATE` | Yes | Yes |
| `surrealdb` | graph + document | BSL 1.1 | No | — | `UPSERT` / `RELATE` | Yes | Yes |

Source-available engines are labeled as such (see
[ADR 0003](../adr/0003-sink-support-policy.md)).

### Capability differences

`SinkCapabilities` declares where engines differ, so the dispatcher adapts rather than
assuming parity:

- `clickhouse` declares `native_upsert=False` (no in-place row update; read-merge-reinsert).
- `duckdb` has no secondary indexes and no `rowcount` (the dialect appends `RETURNING 1`).
- `postgres` declares `arrays=True` and `json=True` (`text[]`, `jsonb`); the other relational
  sinks store JSON text.
- `mongodb`, `neo4j`, and `surrealdb` use the document/graph shapes described in their guides.

## Shared schema

Every relational sink mirrors the same tables and keys:

| Table | Primary key | Purpose |
| ----- | ----------- | ------- |
| `exchange_filing` | `filing_id` | Filing metadata + document payload. |
| `scrape_coverage` | `(chunk_from, chunk_to, run_id)` | One row per scraped chunk. |
| `has_filing` | `(company_id, filing_id)` | Company → filing edges. |
| `references_filing` | `(filing_id, company_id)` | Filing → referenced-company edges. |

A metadata upsert never writes `document_*` columns; a document write only updates an
existing row. Re-running is always idempotent.

## Per-sink guides

- `postgres` — [PostgreSQL](postgresql.md)
- `mysql` — [MySQL and MariaDB](mysql.md)
- `sqlite` — [SQLite](sqlite.md)
- `mongodb` — [MongoDB](mongodb.md)
- `mariadb` — [MySQL and MariaDB](mysql.md) (same driver and schema as `mysql`)
- `neo4j` — [Neo4j](neo4j.md)
- `clickhouse` — [ClickHouse](clickhouse.md)
- `duckdb` — [DuckDB](duckdb.md)
- `surrealdb` — [SurrealDB](surrealdb.md)

## Adding a sink

1. Add a `Dialect` (SQL) or a native adapter in `src/hkex_scraper/sinks/`.
2. Register it in `sinks/registry.py` with its license, OSI status, and optional extra.
3. Add dialect/contract tests and, where a service is required, a CI job.
4. Add it to the support matrix above, to the docs nav, and to the wiki page map; if the
   license is source-available, label it.
