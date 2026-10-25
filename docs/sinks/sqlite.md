---
description: Write HKEx filings, documents, coverage and graph edges to a local SQLite file using only the Python standard library. No extra dependency.
---

# SQLite sink

Persist filings, document payloads, coverage rows, and graph edges to a local SQLite file.

- License: **Public domain** — OSI-approved open source.
- Driver: the Python standard library (`sqlite3`) — **no extra required**.
- Extra: none.

## Install

Nothing beyond the base install:

```bash
pip install .
```

## Configure

```bash
DATABASE_TARGET=sqlite
SQLITE_PATH=hkex.db            # a filesystem path…
# SQLITE_PATH=:memory:         # …or an ephemeral in-memory database
```

Run it:

```bash
hkex-scraper --database-target sqlite --limit 100
```

The schema is created automatically on startup (`CREATE TABLE IF NOT EXISTS` and
`CREATE INDEX IF NOT EXISTS`). No manual DDL.

## Schema

Same tables and keys as every relational sink (see
[sinks overview](README.md#shared-schema)). Datetimes are stored as ISO-8601 `text` and
JSON fields (`document_tables`, `referenced_tickers`) as JSON `text`.

Query it with the `sqlite3` CLI or any SQLite client:

```sql
SELECT company_ticker, count(*) FROM exchange_filing GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

SELECT filing_id, title
FROM exchange_filing
WHERE referenced_tickers LIKE '%0700.HK%';

SELECT filing_id, json_extract(tbl.value, '$.markdown')
FROM exchange_filing, json_each(document_tables) AS tbl
WHERE filing_type = 'Annual Report';
```

## Notes and limitations

- **Single writer**: SQLite serialises writes. It is ideal for local analysis, CI, and
  small deployments; for concurrent production ingestion prefer PostgreSQL or MySQL.
- **Upserts** use `ON CONFLICT DO UPDATE`; edges use `ON CONFLICT DO NOTHING`, so re-runs
  are idempotent.
- **No GIN-style indexes**: JSON columns are stored as text; use `json_extract`/`json_each`
  in queries rather than indexed containment.
- **Foreign keys** are enabled (`PRAGMA foreign_keys = ON`).
- **Tests**: SQLite needs no service, so the sink has in-process integration tests that run
  on every `pytest` invocation (see `tests/test_sqlite_integration.py`).

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `SQLite sink requires SQLITE_PATH` | Path not set | Set `SQLITE_PATH` (or `:memory:`) |
| `unable to open database file` | Directory does not exist / not writable | Create the directory or use an absolute writable path |
| `database is locked` | A second writer holds the file | Ensure one scraper process per database file |
