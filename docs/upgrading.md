# Upgrading

## 1.x → 2.0.0 (breaking)

Version 2.0.0 replaced the two-value `DATABASE_TARGET` (`surrealdb` | `postgres` | `both`)
with an **explicit, ordered, comma-separated list of sink ids**, and added seven sinks
(MySQL/MariaDB, SQLite, MongoDB, Neo4j, ClickHouse, DuckDB).

| Area | Change | Action required |
| ---- | ------ | --------------- |
| `DATABASE_TARGET` | Now a CSV list of ids: `postgres`, `mysql`, `sqlite`, `mongodb`, `mariadb`, `neo4j`, `clickhouse`, `duckdb`, `surrealdb`. | Replace `both` with `surrealdb,postgres`. |
| Default | **Removed.** An unset or unknown value fails fast with the valid ids. | Always set `DATABASE_TARGET`. |
| Read routing | Reads come from the first configured sink that supports them. | Put your preferred read store first in the list. |
| Sink requirements | Every configured sink is required; any write failure exits non-zero. | Configure only sinks you can keep healthy, or accept a non-zero exit on failure. |
| New sinks | `mysql`/`mariadb` (extra `mysql`), `sqlite` (no extra), `mongodb` (extra `mongodb`), `neo4j` (extra `neo4j`), `clickhouse` (extra `clickhouse`), `duckdb` (extra `duckdb`). | Optional. |
| Schema | Unchanged for SurrealDB and PostgreSQL. | None. |

### Migration examples

```ini
# before
DATABASE_TARGET=both

# after (SurrealDB + PostgreSQL)
DATABASE_TARGET=surrealdb,postgres
```

```ini
# before (implicit default, SurrealDB)
# DATABASE_TARGET was unset

# after
DATABASE_TARGET=surrealdb
```

Keeping the previous behavior (SurrealDB primary, PostgreSQL mirrored) is
`DATABASE_TARGET=surrealdb,postgres` — but note that reads now come from `surrealdb`
(first in the list), matching the old default.

### Adopting a new relational sink

1. Install the driver where required: `pip install ".[mysql]"` (SQLite needs nothing).
2. Add the connection settings and the id to the list, e.g.

   ```ini
   DATABASE_TARGET=postgres,sqlite
   SQLITE_PATH=hkex.db
   ```

3. Run `hkex-scraper` as usual. Tables are created automatically.

Multi-write is **forward-only**: data already in one sink is not migrated to another. To
populate a new sink for existing data, re-run the scraper for the desired range (and
`--backfill-docs` for documents).

### Rollback

Set `DATABASE_TARGET` to the sink you want (e.g. `postgres`). Other sinks' tables are
untouched and can be dropped independently.

## 1.0.x → 1.1.0

Version 1.1.0 added the PostgreSQL sink. It was non-breaking: with no new configuration the
scraper behaved as before (SurrealDB only).

| Area | Change | Action required |
| ---- | ------ | --------------- |
| Default sink | SurrealDB | None |
| CLI flags | Two added (`--database-target`, `--parity-report`) | None |
| Schema | PostgreSQL tables added; SurrealDB schema unchanged | None unless using PostgreSQL |
| Dependencies | `psycopg` added as an **optional** extra | `pip install ".[postgres]"` only if wanted |

Adopting PostgreSQL then: set `DATABASE_TARGET=postgres` (or `both`) and `POSTGRES_DSN`,
then run `hkex-scraper`.
