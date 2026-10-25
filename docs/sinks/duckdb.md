---
description: "Write HKEx filings and documents to a DuckDB file or :memory: for local analytics, with ON CONFLICT DO UPDATE. No server required."
---

# DuckDB sink

Persist filings, document payloads, coverage rows, and graph edges to a DuckDB database
file (or `:memory:`).

- License: **MIT** — OSI-approved open source.
- Driver: [`duckdb`](https://pypi.org/project/duckdb/).
- Extra: `duckdb`.

## Install

```bash
pip install ".[duckdb]"
```

## Configure

```bash
DATABASE_TARGET=duckdb
DUCKDB_PATH=hkex.duckdb        # a file path…
# DUCKDB_PATH=:memory:         # …or an ephemeral in-memory database
```

Run it:

```bash
hkex-scraper --database-target duckdb --limit 100
```

The schema is created automatically on startup (`CREATE TABLE IF NOT EXISTS`). No manual DDL.

## Schema

Same tables and keys as every relational sink (see [sinks overview](README.md#shared-schema)).
`document_tables` and `referenced_tickers` are stored as `JSON`; timestamps as `TIMESTAMP`.
Primary keys only — no secondary indexes (DuckDB is analytical).

Query it with the `duckdb` CLI or Python:

```sql
SELECT company_ticker, count(*) FROM exchange_filing GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

SELECT filing_id, title
FROM exchange_filing
WHERE list_contains(from_json(referenced_tickers, '["VARCHAR"]'), '0700.HK');

SELECT filing_id, json_extract(tbl.value, '$.markdown')
FROM exchange_filing, json_each(document_tables) AS tbl
WHERE filing_type = 'Annual Report';
```

## Notes and limitations

- **In-process and file-locked**: DuckDB allows only one writer per database file. Ideal for
  local analysis, notebooks, and CI; for concurrent production ingestion use PostgreSQL or MySQL.
- **Upserts** use `ON CONFLICT DO UPDATE`; edges use `ON CONFLICT DO NOTHING`. The engine has no
  `rowcount`, so the sink uses `RETURNING 1` to count affected rows.
- **JSON is text** in the JSON column type; query with `json_extract` / `json_each`.
- **Tests**: DuckDB runs in-process, so its contract tests run on every `pytest` invocation
  when the extra is installed (`tests/test_duckdb_integration.py`).

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `DuckDB sink requires the duckdb driver` | Extra not installed | `pip install ".[duckdb]"` |
| `DuckDB sink requires DUCKDB_PATH` | Path not set | Set `DUCKDB_PATH` (or `:memory:`) |
| `Could not set lock on file` | Another process holds the database | Ensure a single writer |
