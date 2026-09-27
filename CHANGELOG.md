# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Advanced filtering and metadata browsing on the live MCP gateway.**
  `search_filings` gains `title_query`, `document_type`, `category`, and `stock_name` filters
  alongside `stock_code`, and a new `list_filing_facets` tool browses the categories, document
  types, and stock codes present in a date window, with counts. Search results now carry
  `fileType`, `sizeText`, `category`, and `newsId` metadata, and category values are
  HTML-entity decoded. See [docs/live-mcp.md](docs/live-mcp.md).

### Changed

- **US-English terminology pass across the docs** — UK spellings (`behaviour`, `labelled`,
  `colour`) corrected per [docs/STYLE.md](docs/STYLE.md), and the live-gateway tools are
  documented with filter semantics and runnable examples.

## [2.4.0] - 2026-09-20

### Added

- **AI agent support guide.** [docs/ai-agents.md](docs/ai-agents.md) documents how to connect
  the live gateway from Claude Code, Claude Desktop, ChatGPT, Cursor, VS Code (GitHub
  Copilot), Gemini CLI, opencode, Manus, Perplexity, any MCP SDK client, and stdio-only
  clients via `mcp-remote`.
- **Published to the official MCP Registry** as `io.github.simonplmak-cloud/hkex-filings`;
  `server.json` declares both the hosted remote endpoint and the PyPI package.

### Changed

- **Broader MCP client compatibility.** The gateway negotiates protocol version `2025-03-26`
  in addition to `2024-11-05`/`2025-06-18`/`2025-11-25`, answers `resources/list`,
  `resources/templates/list`, and `prompts/list` with empty lists so probing clients connect
  cleanly, and allows desktop-shell origins (`null`, `file://`) through the Origin allowlist.
  Non-POST requests now return an explanatory body and CORS preflight is answered.
- **Edge rate limiting** on `/api/mcp`: 120 requests per 60 seconds per IP, `429` beyond.
- **Brand and docs refresh** — a new hero/social card and MCP architecture diagram, per-page
  meta descriptions, and a docs home hero.

## [2.3.0] - 2026-09-19

### Added

- **Live MCP gateway (Streamable HTTP).** `src/hkex_scraper/live_mcp.py` exposes the same
  reader as a stateless, database-free HTTP transport with three live tools
  (`get_server_info`, `search_filings`, `get_filing`), fetching fresh data from HKEx on every
  call. The transport is implemented directly — no SDK, no session state, no ASGI lifespan —
  and is guarded by an SSRF host allowlist, an Origin allowlist, and response caps. The
  Vercel entry point is `api/mcp.py`, deployed at `/api/mcp`. See [docs/live-mcp.md](docs/live-mcp.md).

### Changed

- **Documentation is now served by Vercel** at
  <https://hkex-listco-updates.ascent-partners.com/> (the docs site and the live MCP gateway
  share one deployment). GitHub Pages is retired; `mkdocs build --strict` now runs in CI.

## [2.2.0] - 2026-09-19

### Added

- **Optional read-only MCP server.** `pip install "hkex-filing-scraper[mcp]"` provides
  `hkex-scraper-mcp`, a [Model Context Protocol](https://modelcontextprotocol.io) server over
  stdio that exposes a fixed catalog of read-only tools (`search_filings`, `get_filing`,
  `list_tickers`, `count_filings`, `get_coverage`, `get_parity`, `verify_sinks`, and more) so
  LLM clients can read a scraped corpus. It never writes, never scrapes, never runs DDL, and
  imports the `mcp` SDK lazily behind the extra. See [docs/mcp.md](docs/mcp.md).
- **`Sink.fetch_filing_detail()`** — a single-filing read returning metadata plus extracted
  document text and tables, implemented across every adapter (relational dialects, PostgreSQL,
  MongoDB, ClickHouse, Neo4j, SurrealDB). `Sink.fetch_titles()` gained an optional
  case-insensitive `title_query` filter.
- **Composable filing search across every sink.** A typed `FilingQuery` and four new read
  methods (`search_filings`, `search_documents`, `aggregate_filings`, `list_companies`) are
  implemented for all nine sinks. The MCP server gains `search_filings` filters (ticker, stock
  code, title, type, category, status, exchange, referenced ticker, inclusive date range,
  ordering), `search_documents` full-text search with snippets, `get_statistics` counts by
  ticker/type/status, `list_companies`, a generalised `list_pending_filings`, `get_filings`
  batch reads, `get_coverage` totals, and three MCP resources. Search results now carry the
  full filing metadata plus a document summary instead of four columns. No schema changes.
- **Optional PostgreSQL search indexes.** `POSTGRES_FTS_INDEX` (default on) creates
  `pg_trgm` GIN indexes over `lower(title)` and `lower(document_text)` so substring search
  can use an index bitmap scan. Best effort: a user without privilege logs a warning and
  search falls back to a scan, and schema init is never blocked.

## [2.1.0] - 2026-09-17

### Added

- **PyPI release.** The package is now published to PyPI as `hkex-filing-scraper` (`pip install hkex-filing-scraper`), built, checksummed, and attested by a new `pypi.yml` workflow alongside the GitHub release.
- **`--verify` cross-sink reconciliation.** Compares the configured sinks by filing-id set and
  `document_sha256` (not just counts), naming missing/extra ids and hash mismatches and exiting
  non-zero on any difference. `Sink.read_filing_digests()` is implemented for every adapter —
  relational dialects share one query, PostgreSQL, MongoDB, ClickHouse (`FINAL`), Neo4j, and
  SurrealDB each use their own idiom — and a sink that cannot enumerate reports `UNSUPPORTED`
  rather than an empty list, so "no data" is never confused with "cannot tell".

- **API contract fixtures and a daily canary.** `tests/test_api_contract.py` replays the HKEx
  response shapes through the real fetch path (ViewState, form POST, pagination, parsing), and
  `scripts/canary.py` + `.github/workflows/canary.yml` check the live API once a day, opening an
  issue when the shape changes (target: detect a break within 24 hours).
- **Fault-injection tests** (`tests/test_fault_injection.py`) prove per-sink failure isolation:
  a failing or raising sink is counted, never blocks another sink, and makes the run non-zero.
- Tolerant parsing hardened: the API has emitted `null` and bare numbers for string fields, a
  missing JSF form action no longer produces an invalid URL, and a page returning more rows than
  requested can no longer exceed `--limit`.

- **Quality gates.** A coverage gate (`fail_under`, branch coverage, ratcheting upward only),
  property-based tests with Hypothesis over the pure logic, pre-commit hooks (ruff plus
  hygiene checks) enforced in CI, and a job that installs the built wheel into a clean
  environment and runs the suite against it from outside the source tree.
- Tests are now offline by default: `tests/conftest.py` blocks non-loopback connections and a
  `network` marker documents the exception.
- Hypothesis immediately found and fixed a latent chunking bug: `generate_monthly_chunks`
  could return a chunk whose end preceded its start when the range carried a time component.

- Accessibility: a small script names Material's search toggle and makes its scrollable table
  wrappers keyboard reachable, and prose links are now always underlined so colour is never the
  only cue. Together these clear every axe violation (WCAG 2.0/2.1/2.2 A+AA) on the live site.
- **Dark Bloomberg-terminal docs theme.** The site is now fixed dark (amber on near-black,
  monospace throughout, no colour-scheme toggle) with dense tables, ruled code blocks, uppercase
  section headers, visible focus rings, and 24px minimum nav targets. Fonts are the system
  monospace stack — no external font requests. `tests/test_theme.py` computes the WCAG contrast
  ratios from the stylesheet itself and asserts AA, so a palette edit cannot silently ship an
  unreadable page.

- **Data-fidelity guarantees across every sink.** Documents now carry a `document_sha256`
  integrity hash alongside the MD5 identity hash (SurrealDB, PostgreSQL, MySQL/MariaDB, SQLite,
  DuckDB, MongoDB, ClickHouse, Neo4j), mirrored in every schema and the PostgreSQL/relational
  upserts.
- Engine safeguards made explicit: SQLite runs in WAL mode with a 30 s busy timeout, DuckDB
  retries optimistic transaction conflicts, ClickHouse pins `ReplacingMergeTree(updated_at)`
  with `ORDER BY filing_id` and `FINAL` reads, and Neo4j keeps uniqueness constraints on the
  filing and company ids.
- `tests/test_fidelity.py` asserts all of the above, plus utf8mb4 MySQL tables and UTC-aware
  timestamps. `pipeline.document_payload()` is now a pure, testable function.

- **HTTP resilience.** A shared `requests` session now retries connection errors and transient
  statuses (408/429/5xx) up to 4 times with exponential backoff and jitter, honours
  `Retry-After`, and never replays the non-idempotent JSF `POST`. Phase 2 downloads use the same
  session (streaming, with the size cap enforced mid-stream) instead of `urllib`.
- The `User-Agent` now identifies the project and its repository URL instead of impersonating a
  browser, and `REQUEST_DELAY_SECONDS` adds optional process-wide request pacing.
- `tests/test_http.py` covers the retry policy, `Retry-After`, non-retryable statuses, the
  retry budget, POST behaviour, pacing, and the download path.

- **Supply-chain hardening.** Every GitHub Action is pinned to a commit SHA (Dependabot keeps
  them current), workflows declare least-privilege `permissions`, container images are pinned by
  digest, and `uv.lock` pins the full dependency set (`uv lock --check` in CI).
- **Release integrity.** Releases now generate a CycloneDX SBOM and attach signed
  **build-provenance** and **SBOM attestations** (`gh attestation verify`), and ship the SBOM
  alongside the wheel and sdist.
- GitHub Actions bumped to their current majors (checkout v7, setup-python v7, upload-pages-artifact v5, codeql-action v4, gitleaks-action v3), all still pinned by SHA.
- New CI jobs: `Supply chain` (lockfile check, `pip-audit` over the locked set, and a licence
  gate that fails on copyleft dependencies) and **OpenSSF Scorecard**.
- `docs/releasing.md` documents how to verify a release; `SECURITY.md` gains a credential
  rotation runbook and the `wiki` environment gate.

- `docs/STYLE.md` — one style standard for every human-facing doc (US English, sentence-case
  headings, canonical term **sink** vs **engine**), enforced by a new `markdownlint-cli2` CI job.
- Community files: `SUPPORT.md`, `GOVERNANCE.md`, `.github/FUNDING.yml`, and
  `.github/ISSUE_TEMPLATE/config.yml`; `CODE_OF_CONDUCT.md` now embeds the full
  Contributor Covenant 2.1 text, and `SECURITY.md` gains a supported-versions table, response
  targets, scope, and a safe-harbor statement.
- Wiki mirror: `scripts/mirror_wiki.py` renders `docs/` into GitHub wiki pages (with a generated
  sidebar and rewritten links) and `.github/workflows/wiki.yml` pushes them on every change to
  `docs/`. It is a no-op until the `WIKI_TOKEN` secret exists.
- Docs site: mermaid diagrams now render (`pymdownx.superfences` custom fences), plus a logo and
  favicon. `docs/assets/banner.svg` and a regenerated `docs/social_preview.png` show every sink.
- `docs/sinks/surrealdb.md` — SurrealDB now has a dedicated guide, so every sink id has one page.
- PEP 561 marker (`src/hkex_scraper/py.typed`), PEP 639 licence metadata
  (`license = "MIT"` + `license-files`), and `Documentation`/`Changelog` entries in
  `[project.urls]`.
- README "How it works" mermaid diagram, badges for every sink, and a per-link table of contents.
- De-risking register rows R14–R18 (AGPL exposure, cross-sink semantics, reproducibility,
  repository controls, shared-PAT blast radius).

### Changed

- **Documented order is now popularity order** (`postgres`, `mysql`, `sqlite`, `mongodb`,
  `mariadb`, `neo4j`, `clickhouse`, `duckdb`, `surrealdb`) everywhere: the registry, README,
  docs, mkdocs nav, `.env.example`, CLI help, and the wiki sidebar. It is declared once in
  `sinks/registry.py:POPULARITY_ORDER` and asserted by the docs-consistency test.
- **All sinks are presented as first-class.** Tier language is gone from the docs, CI job names,
  and test module names. `DEFAULT_SINK` is removed, the README no longer calls any sink
  "recommended", and the default `.env.example` target is a neutral two-sink example.
- `docs/backends/` is renamed to `docs/sinks/`; `docs/postgresql.md` moves to
  `docs/sinks/postgresql.md`; `docs/architecture.md` is engine-agnostic.
- The `pdf` extra drops the obsolete `camelot-py[cv]` extra (camelot 2.x has no `cv` extra).
- **The `pdf` extra (PyMuPDF, pymupdf4llm) is no longer part of `[all]`** — it is AGPL-3.0 and
  is now installed only on request, with the licence disclosed in the README, `docs/legal.md`,
  and `pyproject.toml`.
- Test modules renamed to drop tier language: `test_extra_sinks.py`,
  `test_extra_sinks_integration.py`, `test_multi_sink_integration.py`; CI jobs renamed to
  `integration-sinks-extra` and `integration-multi-sink`.
- Documentation accuracy fixes: the README no longer claims recursive retries (HTTP retry and
  backoff are tracked in the risk register), `constitution.md` lists all nine sinks, and
  `docs/testing.md` is reordered and covers MySQL/MariaDB.
- Terminology and spelling normalized across the docs and community files (US English,
  "sink" for a configured destination, "engine" for a database product).
- The repository homepage now points at the docs site.

## [2.0.0] - 2026-09-16

### Added

- **MongoDB, DuckDB, ClickHouse, and Neo4j sinks.** Four more sinks implement the same `Sink` contract, each an optional extra with a guarded import:
  - `duckdb` (MIT; `duckdb` extra) — in-process analytical SQL, `ON CONFLICT` upserts, JSON columns, PK-only schema.
  - `mongodb` (SSPL, source-available; `mongodb` extra) — document model, collections keyed on `_id`, `$set` upserts; metadata and document writes never touch each other's fields.
  - `clickhouse` (Apache-2.0; `clickhouse` extra) — columnar `ReplacingMergeTree`; declares `native_upsert=False` and uses read-merge-reinsert so metadata and document writes preserve each other; reads use `FINAL`.
  - `neo4j` (GPLv3 Community; `neo4j` extra) — graph model with `(:Company)-[:HAS_FILING]->(:Filing)` and `(:Filing)-[:REFERENCES_FILING]->(:Company)`, all writes via `MERGE`; `documentTables` stored as JSON.
- **Uniform sink architecture.** A new `sinks/` package defines one destination contract (`Sink`), declared capabilities (`SinkCapabilities`), and a lazy registry, so the pipeline, graph linker, and CLI no longer branch on a destination name. Backends are hand-written adapters plus a small SQL dialect descriptor; there is no ORM.
- **MySQL and MariaDB sinks** (`mysql`, `mariadb`) via the optional `PyMySQL` driver (`pip install ".[mysql]"`), with `ON DUPLICATE KEY UPDATE` upserts and `INSERT IGNORE` edge inserts.
- **SQLite sink** (`sqlite`) using the standard-library `sqlite3` driver — no extra dependency.
- Per-sink capability model (upsert, reads, edges, JSON, arrays, limits) and a support matrix at `docs/sinks/README.md`.
- `docs/adr/0002-multi-sink-architecture.md`, `docs/adr/0003-sink-support-policy.md`, and per-sink guides under `docs/sinks/` (mysql, sqlite, duckdb, mongodb, clickhouse, neo4j).
- CI job running MongoDB, ClickHouse, and Neo4j service containers.
- **MySQL/MariaDB live integration test and CI job** (`integration-mysql`), closing the last sink without live coverage.
- **`install-matrix` CI job** exercising the documented `pip install .` and `pip install ".[all]"` installs on a clean runner.
- **Docs-consistency test** (`tests/test_docs_consistency.py`) that fails a PR when a sink is added or renamed without updating the README, CLI reference, configuration reference, issue templates, sink guides, `pyproject` extras, or `.env.example`.
- Governance/security: Dependabot, CodeQL workflow, secret scan in CI, `CODEOWNERS`, `CITATION.cff`, `.gitattributes`, `.editorconfig`, and a CI secret-scan job.
- `examples/` — a `docker-compose.yml` with PostgreSQL, MySQL, MariaDB, MongoDB, ClickHouse, Neo4j, and SurrealDB, plus `quickstart.sh` and per-sink connection settings.
- Docs: `docs/legal.md` (Terms of Use and data responsibilities), `docs/de-risking.md` (risk register), a GitHub Pages docs site (`mkdocs.yml`), and a release **rollback runbook**.
- Release workflow now also verifies that an optional extra installs from the wheel and registers its sink.

### Changed

- **Breaking:** `DATABASE_TARGET` is now an explicit, ordered, comma-separated list of sink ids (`postgres`, `mysql`, `mariadb`, `sqlite`, `duckdb`, `mongodb`, `clickhouse`, `neo4j`, `surrealdb`). The `both`/`dual` aliases and the implicit `surrealdb` default are removed; an unset or unknown value fails fast with an actionable message.
- **Breaking:** reads are served by the first configured sink that supports reads; there is no per-sink read branch and no `READ_SOURCE` variable.
- Every explicitly configured sink is now required: any sink's write failure marks the run non-zero (previously a non-sole PostgreSQL sink could fail without failing the run). Failure isolation is unchanged.
- `--parity-report` is now N-way: it prints a filing count per sink and the spread between the maximum and minimum.
- SurrealDB RPC body-size truncation moved from the pipeline into the SurrealDB sink; the pipeline now builds one canonical payload and each sink applies its own declared limit.

### Fixed

- Integration fixtures now initialise the schema of **every** configured sink (matching `main()`), fixing "no such table" failures when a second sink was configured during tests.

## [1.1.0] - 2026-09-16

### Added

- **PostgreSQL sink (dual-store).** `DATABASE_TARGET` selects `surrealdb` (default), `postgres`, or `both`. The scraper mirrors filing metadata, document payloads (`document_tables` as `jsonb`, `referenced_tickers` as `text[]`), coverage rows, and graph edges into PostgreSQL with idempotent `ON CONFLICT` upserts. Schema is created automatically with `CREATE TABLE IF NOT EXISTS`.
- `--database-target` CLI override and `--parity-report` (per-sink filing counts and difference).
- `postgres` optional extra (`psycopg[binary,pool]>=3.1`), included in `all`.
- Per-sink success/failure counters and a sink summary printed every run; non-zero exit when a required sink fails.
- Documentation: `docs/` (getting started, PostgreSQL guide, configuration, CLI, architecture, troubleshooting, upgrading), `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and PR templates, and this changelog.
- CI workflow (lint, format check, tests on Python 3.10–3.13, optional PostgreSQL integration job).

### Changed

- Composite `scrape_coverage` table added to the SurrealDB schema; `scrape_coverage` mirrored to PostgreSQL.
- Sink reads are routed by target: with `DATABASE_TARGET=postgres`, Phase 2 pending-filing selection and graph ticker enumeration read from PostgreSQL.
- Timestamps written to `timestamptz` columns are now timezone-aware (UTC).
- PostgreSQL connection strings assembled from discrete settings are URL-encoded.
- Pinned the ruff rule selection and version range (`ruff>=0.16,<0.17`) to keep `ruff check` stable across ruff releases.

### Fixed

- Fail fast with an actionable message when PostgreSQL is the only configured sink but the driver or connection details are missing (previously the run could appear to succeed while writing nothing).
- Accurate edge insert counts (`created`) via affected-row counts instead of attempted-row counts.

### Notes

- Non-breaking: the default sink and all existing CLI flags are unchanged. Multi-sink is forward-only — existing SurrealDB data is not migrated to PostgreSQL automatically.

[Unreleased]: https://github.com/simonplmak-cloud/hkex-filing-scraper/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/simonplmak-cloud/hkex-filing-scraper/releases/tag/v2.0.0
[1.1.0]: https://github.com/simonplmak-cloud/hkex-filing-scraper/releases/tag/v1.1.0
