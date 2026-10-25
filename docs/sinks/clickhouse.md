---
description: "Write HKEx filings and documents to ClickHouse over HTTP, using ReplacingMergeTree plus read-merge-reinsert for idempotency. Table schema and connection variables."
---

# ClickHouse sink

Persist filings, document payloads, coverage rows, and graph edges to ClickHouse.

- License: **Apache-2.0** — OSI-approved open source.
- Driver: [`clickhouse-connect`](https://pypi.org/project/clickhouse-connect/) (HTTP).
- Extra: `clickhouse`.

## Install

```bash
pip install ".[clickhouse]"
```

## Configure

```bash
DATABASE_TARGET=clickhouse
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=hkex
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=secret
```

Run it:

```bash
hkex-scraper --database-target clickhouse --limit 100
```

Tables are created automatically on startup.

## Schema

Tables use `ReplacingMergeTree` with a sort key; reads use `FINAL`:

| Table | `ORDER BY` | Notes |
| ----- | ---------- | ----- |
| `exchange_filing` | `filing_id` | `document_tables` is `Nullable(String)` (JSON); `referenced_tickers` is `Array(String)`. |
| `scrape_coverage` | `(chunk_from, chunk_to, run_id)` | One row per chunk. |
| `has_filing` | `(company_id, filing_id)` | Company → filing edges. |
| `references_filing` | `(filing_id, company_id)` | Filing → referenced-company edges. |

```sql
SELECT company_ticker, count() FROM exchange_filing FINAL GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

SELECT filing_id, title
FROM exchange_filing FINAL
WHERE has(referenced_tickers, '0700.HK');

SELECT filing_id, JSONExtractString(table, 'markdown')
FROM exchange_filing FINAL
ARRAY JOIN JSONExtractArrayRaw(document_tables) AS table
WHERE filing_type = 'Annual Report';
```

## Notes and limitations

- **No row-level upsert.** ClickHouse has no `UPDATE`/`ON CONFLICT`. This sink uses
  `ReplacingMergeTree` (duplicates collapse on merge; reads use `FINAL`) plus
  **read-merge-reinsert**: a metadata write preserves the existing `document_*` columns and a
  document write preserves the metadata. It therefore declares `native_upsert=False`, and
  writes are slower (one read per filing) than the other relational sinks.
- **Deletes/updates are eventual.** `FINAL` returns deduplicated results; background merges
  are not guaranteed before a read, so `FINAL` is used for correctness.
- **`created` edge counts** are rows submitted, not rows after dedup.
- **Not ideal as the primary ingestion target** for high-volume concurrent writes; excellent
  for analytical queries over a mirror.

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `ClickHouse sink requires clickhouse-connect` | Extra not installed | `pip install ".[clickhouse]"` |
| `ClickHouse sink requires CLICKHOUSE_HOST` | Host not set | Set `CLICKHOUSE_HOST` |
| `ClickHouse sink requires CLICKHOUSE_DATABASE` | Database not set | Create it and set `CLICKHOUSE_DATABASE` |
| `Authentication failed` | Wrong user/password | Check `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` |
