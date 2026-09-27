---
description: Command-line reference for hkex-scraper — every flag, the exit codes, and examples.
---

# CLI Reference

```text
hkex-scraper [options]
```

## Options

| Option | Type | Default | Description |
| ------ | ---- | ------- | ----------- |
| `--full-history` | flag | off | Scrape all filings from April 1999 to today. |
| `--from-date` | `DD/MM/YYYY` | ~2 months ago | Start of the scrape range. |
| `--to-date` | `DD/MM/YYYY` | today | End of the scrape range. |
| `--limit N` | int | `0` (unlimited) | Process at most `N` filings. |
| `--metadata-only` | flag | off | Phase 1 only — no document downloads. |
| `--backfill-docs` | flag | off | Phase 2 only — process documents for filings already in the database. |
| `--link-only` | flag | off | Only create/refresh graph edges. |
| `--dry-run` | flag | off | Fetch data but write nothing. |
| `--database-target SINKS` | comma-separated sink ids | from `DATABASE_TARGET` | Override the sinks for this run. Valid: `postgres`, `mysql`, `sqlite`, `mongodb`, `mariadb`, `neo4j`, `clickhouse`, `duckdb`, `surrealdb`. Order sets read precedence. |
| `--coverage-report` | flag | off | Print chunk coverage from the read source, then exit. |
| `--parity-report` | flag | off | Print per-sink filing counts and the spread, then exit. Requires two or more sinks. |
| `--verify` | flag | off | Compare every configured sink by filing id and document hash (`document_sha256`), then exit non-zero on any difference. Requires two or more sinks. |
| `--version` | flag | — | Print the version and exit. |

## Behavior

- With no mode flag, a full run is performed: Phase 1 (metadata), then graph linking, then Phase 2 (documents) — unless `--metadata-only`.
- `--backfill-docs` and `--link-only` run phases in isolation for incremental maintenance.
- `--coverage-report`, `--parity-report`, and `--verify` are read-only reports that exit after printing.
- A `--limit` also caps the number of documents Phase 2 processes.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| `0` | Success — every configured sink's writes succeeded. |
| `1` | A configured sink failed a write, `--verify` found a difference between sinks, or configuration is invalid/unusable (unset/unknown `DATABASE_TARGET`, missing driver or connection details). |

## Examples

```bash
# Backfill documents only, 50 at a time
hkex-scraper --backfill-docs --limit 50

# Rebuild graph edges from existing filings
hkex-scraper --link-only

# Write to SQLite only for this run
hkex-scraper --database-target postgres --limit 100

# Validate config without writing
hkex-scraper --dry-run --limit 10

# Coverage and parity
hkex-scraper --coverage-report
hkex-scraper --database-target postgres,sqlite --parity-report

# Cross-sink reconciliation (filing ids + document hashes)
hkex-scraper --database-target postgres,sqlite --verify

# Version
hkex-scraper --version
```

## Logs

Each run writes a timestamped log to `logs/hkex_filings_<YYYY-MM-DD_HH-MM>.log` in the working directory, and failed SurrealDB statements are appended to `logs/hkex_failed.sql`. Credentials are never written to either file.
