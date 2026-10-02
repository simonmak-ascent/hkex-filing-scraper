---
description: What HKEx Filing Scraper has shipped, what is next, and what is under consideration.
---

# Roadmap

Where the project is going. Items move from **Next** to **Shipped** as they land; the
tracked risks and their mitigations are in the [De-risking register](de-risking.md).

## Shipped

- **Nine sinks on one contract** — PostgreSQL, MySQL/MariaDB, SQLite, MongoDB, Neo4j,
  ClickHouse, DuckDB, and SurrealDB, each with idempotent upserts and per-sink failure
  isolation.
- **Read-only MCP server** (`hkex-scraper-mcp`) over stdio for a stored corpus, with a
  fixed catalog of read tools and three MCP resources.
- **Hosted live MCP gateway** — a stateless, database-free Streamable HTTP endpoint that
  fetches HKEx on every call, with [AI-agent support](ai-agents.md) for the popular clients.
- **Resilience** — real HTTP retry/backoff, replayed-shape API contract tests, a daily live
  canary that opens an issue on a response change, fault-injection tests, and `--verify`
  cross-sink reconciliation.
- **Supply chain** — every Action pinned to a commit SHA, `pip-audit` over the locked set, a
  copyleft license gate, CycloneDX SBOM plus build-provenance attestations, and OpenSSF
  Scorecard.

## Next

- **Deprecation shim for breaking changes** — a compatibility layer and a migration note when
  a pinned interface changes (register R6).
- **Performance budget and run report** — a benchmark guard and a machine-readable summary at
  the end of a run (register R12).

## Considered

- **Additional sinks** — OpenSearch, Cassandra, Valkey, TiDB. Acceptance criteria are in
  [ADR 0003](adr/0003-sink-support-policy.md); open-source engines come first.

Contributions that fit the roadmap — and `good first issue` items — are especially welcome;
see [CONTRIBUTING](https://github.com/simonmak-ascent/hkex-filing-scraper/blob/main/CONTRIBUTING.md).
