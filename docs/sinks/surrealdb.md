---
description: "Write HKEx filings and document payloads to SurrealDB over its HTTP API using SurrealQL. Record model, SCHEMAFULL tables and the payload limits."
---

# SurrealDB sink

Persist filings, documents, coverage rows, and graph edges to SurrealDB over its HTTP API,
using SurrealQL.

- License: **BSL 1.1** — source-available, not OSI-approved (see
  [ADR 0003](../adr/0003-sink-support-policy.md)).
- Driver: none — the adapter uses the standard-library HTTP client via `requests`.
- Extra: none.

## Install

No extra dependency. `pip install .` is enough.

## Configure

```ini
DATABASE_TARGET=surrealdb        # or a list, e.g. postgres,surrealdb
SURREAL_ENDPOINT=http://localhost:8000
SURREAL_NAMESPACE=default
SURREAL_DATABASE=default
SURREAL_USERNAME=root
SURREAL_PASSWORD=secret
```

A throwaway server:

```bash
docker run -d --name surrealdb -p 8000:8000 \
  surrealdb/surrealdb:v3.2.4 start --log warn --user root --pass root --bind 0.0.0.0:8000 memory
```

Run it:

```bash
hkex-scraper
```

## Schema

Created automatically at startup; every statement is `IF NOT EXISTS`, so it is safe to re-run.

| Table | Key | Notes |
| ----- | --- | ----- |
| `exchange_filing` | `filingId` (16-char MD5) | `SCHEMAFULL`. Metadata plus the document payload (`documentText`, `documentTables`, `documentStatus`, …). |
| `scrape_coverage` | one row per chunk | `SCHEMAFULL`; `apiCount` / `ingestedCount` / `uniqueCount` plus `runId`. |
| `has_filing` | company → filing | Graph edge, created with `RELATE`. |
| `references_filing` | filing → company | Graph edge, created with `RELATE`. |

`documentTables` is `option<array<object>>` with typed sub-fields (`tableIndex`, `sheetName`,
`pageNumber`, `headers`, `rowCount`, `markdown`). The obsolete `documentContent` blob is
removed on every startup.

## Notes and limitations

- **Two endpoints.** Writes go to `/rpc` (parameterised JSON, ~4 MiB body limit) and fall
  back to `/sql` (~1 MiB body limit, string-escaped SQL) when needed.
- **Record-link parsing.** `/rpc` interprets JSON strings shaped like `word:word` as record
  links, which would corrupt `option<string>` fields. The adapter detects this and retries the
  write through `/sql`.
- **Truncation.** If the `/rpc` payload would exceed ~3.8 MB, `documentText` is pre-truncated
  (preserving tables where possible); extreme cases fall through to `/sql` with further
  truncation and no tables. The reason is recorded in `documentStatusReason`.
- **SCHEMAFULL.** New fields must be added to the schema DDL in `db.py:_build_schema_sql()`;
  SurrealDB rejects unknown fields on a `SCHEMAFULL` table.
- **Company matching for edges.** An edge is created only for a ticker that exists in the
  configured company table (`COMPANY_TABLE`). MongoDB, Neo4j, and the relational sinks create
  edges for every ticker.
- **Credentials.** The endpoint and credentials are never logged.

## Example queries

```surql
-- Filings per company
SELECT companyTicker, count() AS filings
FROM exchange_filing
GROUP BY companyTicker
ORDER BY filings DESC
LIMIT 20;

-- Filings that reference a ticker
SELECT filingId, title, filingDate
FROM exchange_filing
WHERE '0700.HK' INSIDE referencedTickers;

-- Coverage
SELECT chunkFrom, chunkTo, apiCount, ingestedCount, uniqueCount, runId
FROM scrape_coverage
ORDER BY chunkFrom DESC;
```

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `record link` / `option<string>` errors | `/rpc` parsed a `word:word`-shaped string as a record link. | Automatic — the adapter retries via `/sql`. If it still fails, check `logs/hkex_failed.sql`. |
| Document text is truncated | Payload exceeded the `/rpc` body limit. | Expected; the reason is stored in `documentStatusReason`. Increase the truncation budget only if your server accepts larger bodies. |
| `Found field not in schema` | A new field was written to the `SCHEMAFULL` table before the DDL was updated. | Add the field to `db.py:_build_schema_sql()`; the DDL runs at startup. |
| Connection refused | Server not running, or wrong `SURREAL_ENDPOINT`. | Start the server (see above) and confirm the endpoint. |
| Authentication failed | Wrong `SURREAL_USERNAME` / `SURREAL_PASSWORD` / namespace / database. | Check the four values; the namespace and database default to `default`. |

## See also

- [Sink overview and support matrix](README.md)
- [Configuration reference](../configuration.md)
- [Architecture](../architecture.md)
