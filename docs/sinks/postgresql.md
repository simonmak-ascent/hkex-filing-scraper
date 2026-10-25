---
description: "Write HKEx filings, document payloads, coverage rows and graph edges to PostgreSQL as the only sink or alongside others. Schema, DSN variables, upsert behaviour and indexing."
---

# PostgreSQL sink

Mirror every record — filings, document payloads, coverage rows, and graph edges — into
PostgreSQL, either as the only sink or alongside others (multi-write).

- License: **PostgreSQL License** — OSI-approved open source.
- Driver: `psycopg` 3 (`psycopg[binary,pool]`).
- Extra: `postgres`.

## Install

```bash
pip install ".[postgres]"     # psycopg[binary,pool]>=3.1
```

## Configure

Select the sink in `.env`, either with a full DSN:

```ini
DATABASE_TARGET=postgres          # or a list, e.g. postgres,sqlite
POSTGRES_DSN=postgresql://user:password@localhost:5432/hkex
```

or with discrete settings:

```ini
DATABASE_TARGET=postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DATABASE=hkex
POSTGRES_USER=hkex
POSTGRES_PASSWORD=secret
POSTGRES_SCHEMA=public        # optional, default "public"
```

`POSTGRES_DSN` takes precedence over the discrete settings. When the DSN is assembled from
discrete settings, the password is URL-encoded automatically.

Run it:

```bash
hkex-scraper
```

The schema is created on startup with `CREATE TABLE IF NOT EXISTS` — no manual DDL, and safe
to re-run.

## Schema

| Table | Key | Notes |
| ----- | --- | ----- |
| `exchange_filing` | `filing_id` (MD5-16, PK) | Metadata + document payload. `document_tables` → `jsonb`, `referenced_tickers` → `text[]`, `filing_date`/`updated_at` → `timestamptz`. |
| `scrape_coverage` | `(chunk_from, chunk_to, run_id)` unique | Chunk completeness rows. |
| `has_filing` | `(company_id, filing_id)` PK | Company → filing edges. |
| `references_filing` | `(filing_id, company_id)` PK | Filing → referenced-company edges. |

`filing_id` is the same identifier used by SurrealDB, so it is a reliable cross-sink join key.

## Notes and limitations

- **Idempotency.** Filings, coverage, and edges use `INSERT ... ON CONFLICT ... DO UPDATE` (or
  `DO NOTHING` for edges). Re-running updates existing rows and creates no duplicates.
- **No clobbering.** The metadata upsert never touches `document_*` columns; document payloads
  use a scoped `UPDATE`. A `--metadata-only` re-run will not overwrite extracted text or tables.
- **Failure isolation.** A PostgreSQL failure never aborts or rolls back writes to other sinks
  (and vice versa). Per-sink success/failure counts are printed at the end of every run.
- **Every configured sink is required.** If PostgreSQL is configured and unusable (driver or
  connection details missing), the run fails fast with an actionable error rather than
  appearing to succeed.
- **Read routing.** Reads (pending filings, distinct tickers, titles, coverage) come from the
  first configured sink that supports them — put `postgres` first in `DATABASE_TARGET` to read
  from PostgreSQL.
- **Credentials.** The DSN is never logged; `db_postgres` redacts `password=` and URL
  credentials from error text.
- **Edges.** Graph edges are written only when `COMPANY_TABLE` is configured. The SurrealDB
  sink creates an edge only for tickers matched in its company table; the relational sinks
  create edges for every ticker.
- **Pooling.** `POSTGRES_MIN_POOL` / `POSTGRES_MAX_POOL` size the connection pool (defaults
  1 / 15).
- **Parity.** `hkex-scraper --database-target postgres,sqlite --parity-report` prints the filing
  count per sink and the spread (`Parity: OK` on zero). With a single sink, parity is `N/A`.

## Example queries

```sql
-- Filings per company
SELECT company_ticker, count(*) AS filings
FROM exchange_filing
GROUP BY company_ticker
ORDER BY filings DESC
LIMIT 20;

-- Filings that reference a ticker
SELECT filing_id, title, filing_date
FROM exchange_filing
WHERE referenced_tickers && ARRAY['0700.HK']
ORDER BY filing_date DESC;

-- Inspect extracted tables (JSONB)
SELECT filing_id, elem->>'pageNumber' AS page, left(elem->>'markdown', 200) AS preview
FROM exchange_filing,
     jsonb_array_elements(document_tables) AS elem
WHERE filing_type = 'Annual Report'
LIMIT 5;

-- Document processing status distribution
SELECT document_status, count(*) FROM exchange_filing GROUP BY 1;

-- Coverage report
SELECT chunk_from, chunk_to, api_count, ingested_count, unique_count, run_id
FROM scrape_coverage
ORDER BY chunk_from DESC;
```

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `No module named 'psycopg'` | Driver not installed. | `pip install ".[postgres]"`. |
| `password authentication failed` | Wrong credentials, or an unescaped character in the password. | Check `POSTGRES_DSN`, or switch to discrete settings (the password is URL-encoded for you). |
| `connection refused` | Server not running, or wrong host/port. | Confirm the server is up and `POSTGRES_HOST`/`POSTGRES_PORT` are correct. |
| `relation "exchange_filing" does not exist` | Writes and reads point at different schemas. | Set `POSTGRES_SCHEMA` to the schema you query, or query the schema the scraper created. |
| Run exits non-zero after some writes | PostgreSQL was one of several sinks and it failed. | Check the per-sink summary printed at the end of the run; other sinks are unaffected. |

## See also

- [Sink overview and support matrix](README.md)
- [Configuration reference](../configuration.md)
- [Architecture](../architecture.md)
