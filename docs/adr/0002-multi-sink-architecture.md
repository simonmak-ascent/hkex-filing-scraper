---
description: "ADR 0002: one Sink contract, declared SinkCapabilities, lazy driver imports and an ordered registry, so a new database needs no change to the pipeline."
---

# ADR 0002 — Multi-sink architecture

- Status: Accepted
- Date: 2026-09-16
- Deciders: maintainers

## Context

The scraper started with one destination (SurrealDB) and gained a second (PostgreSQL) by
adding per-sink branches in `config.py`, `pipeline.py`, `graph.py`, and `main.py`. There
were ~96 references to `surrealdb_enabled()` / `postgres_enabled()` / `db_postgres.*`
spread across four modules. Adding a third destination meant editing all four files, and
the pipeline embedded destination-specific SQL (`UPSERT exchange_filing:…`) next to
destination-agnostic logic (truncation, classification).

The project wants to support more open-source engines (MySQL/MariaDB, SQLite now;
MongoDB, DuckDB, ClickHouse, Neo4j later) without turning the four core modules into a
switchboard.

## Decision

- Introduce a `sinks/` package with **one uniform contract** (`Sink`),
  **declared capabilities** (`SinkCapabilities`), and a **lazy registry**
  (`registry.SINKS`) so no optional driver is imported until its sink is requested.
- Implement sinks as **hand-written adapters** plus a small **SQL dialect
  descriptor** (`Dialect`). There is no ORM and no plugin-discovery machinery.
- Keep the relational engines (MySQL/MariaDB, SQLite) on a shared execution loop
  (`RelationalSink`); keep PostgreSQL on its existing, already-tested module
  (`db_postgres`) behind a thin `PostgresSink` adapter; keep SurrealDB's SurrealQL,
  `RELATE`, and record-link/RPC fallbacks inside `SurrealDBSink`.
- The pipeline builds **one canonical record** per filing/document/coverage/edge and hands
  it to **every configured sink**. Sinks never see the scraper's internals.
- `DATABASE_TARGET` becomes an **explicit, ordered, comma-separated list** of sink ids.
  The order defines read precedence: reads are served by the **first configured sink whose
  capabilities include `reads`**. There is no `READ_SOURCE` variable and no silent default.
- Every explicitly configured sink is **required**: any sink's write failure marks the run
  non-zero. Failures are still isolated — one sink never blocks or rolls back another.

## Alternatives considered

| Alternative | Why not chosen |
| ----------- | -------------- |
| Keep adding `if postgres_enabled(): …` branches | Four-file edit per sink; the class of bug this ADR removes. |
| SQLAlchemy / an ORM | Constitution prohibits dependency creep; an ORM is heavier than the four dialects in use and hides the DDL we want to assert in tests. |
| Entry-point plugin discovery | Runtime plugin failures are hard to diagnose and it adds packaging machinery for a handful of first-party sinks. |
| One shared schema-less "document" store for all sinks | Loses each engine's strengths (typed columns, `jsonb`, graph edges) and makes parity reporting meaningless. |
| Independent per-sink modules with no shared core | Duplicates batching, retries, redaction, and degradation logic across every sink. |

## Consequences

- Adding a sink is a localised change: one adapter (or dialect entry) + one registry
  entry + one test file.
- Capability differences are explicit, so the dispatcher adapts instead of pretending
  parity. ClickHouse's lack of native upsert, for example, will be declared rather than
  hidden.
- `DATABASE_TARGET` is a **breaking** change: `both`/`dual` and the implicit `surrealdb`
  default are gone; an unset value is a hard error. This was an accepted trade-off.
- SurrealDB edges remain partly sink-specific (company record-id resolution and `RELATE`),
  which is why they live in `SurrealDBSink` rather than the generic path.
- SurrealDB's RPC body-size truncation moved from the pipeline into `SurrealDBSink`; the
  pipeline now passes the full canonical payload and each sink applies its own declared
  limit.
