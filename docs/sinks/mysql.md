---
description: Write HKEx filings and document payloads to MySQL or MariaDB with ON DUPLICATE KEY UPDATE. DSN variables, schema, and the GPLv2 licence note.
---

# MySQL and MariaDB sink

Persist filings, document payloads, coverage rows, and graph edges to MySQL or MariaDB.

- License: **GPLv2** (MySQL Community / MariaDB) — OSI-approved open source.
- Driver: [`PyMySQL`](https://pypi.org/project/PyMySQL/) (pure Python, no C toolchain).
- Extra: `mysql`.

## Install

```bash
pip install ".[mysql]"
```

## Configure

```bash
DATABASE_TARGET=mysql          # or: mariadb, or postgres,mysql
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DATABASE=hkex
MYSQL_USER=hkex
MYSQL_PASSWORD=secret
# optional single-string form (takes precedence)
# MYSQL_DSN=mysql://hkex:secret@localhost:3306/hkex
```

The `mariadb` sink reads `MARIADB_*` and falls back to `MYSQL_*` when its own variables are
absent, so one set of credentials can serve both.

```bash
DATABASE_TARGET=mariadb
MARIADB_HOST=localhost
MARIADB_DATABASE=hkex
MARIADB_USER=hkex
MARIADB_PASSWORD=secret
```

Run it:

```bash
hkex-scraper --database-target mysql --limit 100
```

The schema is created automatically on startup with `CREATE TABLE IF NOT EXISTS` (indexes
are declared inline, because MySQL has no `CREATE INDEX IF NOT EXISTS`). No manual DDL.

## Schema

Same tables and keys as every relational sink (see
[sinks overview](README.md#shared-schema)). `document_tables` and `referenced_tickers`
are stored as `json`; long text is `longtext`; timestamps are `datetime(6)`.

## Notes and limitations

- **Upserts** use `ON DUPLICATE KEY UPDATE`, so re-running updates instead of duplicating.
- **Edges** use `INSERT IGNORE`, so an existing edge is a no-op.
- **Engine**: InnoDB with `utf8mb4` (required for the bilingual titles and text).
- **Credentials** are never logged; error text returned to the caller is redacted.

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `MySQL sink requires PyMySQL` | Extra not installed | `pip install ".[mysql]"` |
| `MySQL sink requires MYSQL_HOST/...` | Missing connection settings | Set `MYSQL_*` or `MYSQL_DSN` |
| `Access denied for user` | Wrong credentials | Recheck `MYSQL_USER` / `MYSQL_PASSWORD` |
| `Unknown database` | Database not created | `CREATE DATABASE hkex CHARACTER SET utf8mb4;` |
| `Data too long for column` | A value exceeds a `varchar(255)` column | Report it — long fields use `longtext` |
