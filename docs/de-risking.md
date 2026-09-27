# De-risking register

Living document. Reviewed at every release. Scores are **L×I** (likelihood × impact, 1–5).
`Status` tracks which horizon the control belongs to: **C1** = this release, **C2** = next
(API resilience), **C3** = scheduled/structural.

| ID | Risk | L | I | Score | Control (current / planned) | Status |
|----|------|---|---|-------|-----------------------------|--------|
| R1 | Undocumented HKEx API breaks (JSF/ViewState, `rowRange`, silent format change) | 4 | 5 | **20** | tolerant parsing, per-chunk coverage, real HTTP retries · **added:** replayed-shape contract tests (`tests/test_api_contract.py`) and a daily live canary (`scripts/canary.py`, `canary.yml`) that opens an issue on a shape change | C1 |
| R2 | Cross-sink data loss / corruption (truncation, partial writes, ClickHouse merge) | 2 | 5 | 10 | idempotent upserts, no-clobber, per-sink counters · **added:** fault-injection tests (`tests/test_fault_injection.py`) proving failure isolation and non-zero exit; `--verify` semantic reconciliation | C1 |
| R3 | Solo bus factor / governance | 4 | 4 | 16 | docs, CONTRIBUTING, templates · **added:** CODEOWNERS, Discussions, roadmap, good-first-issue | C1 |
| R4 | Docs ↔ code drift (already observed) | 4 | 3 | 12 | manual review · **added:**`tests/test_docs_consistency.py` | C1 |
| R5 | CI not enforced on `main` | 3 | 4 | 12 | CI on PRs · **added:** branch protection (required checks + conversation resolution) | C1 |
| R6 | Breaking change adoption friction | 4 | 3 | 12 | `docs/upgrading.md`, fail-fast message · **added:** v2.0.0 migration table; **planned:** deprecation shim | C1/C3 |
| R7 | Legal / HKEx Terms of Use | 2 | 5 | 10 | README note · **added:**`docs/legal.md` + prominent README section | C1 |
| R8 | Secrets leakage | 2 | 5 | 10 | secret scanning, `redact()` tests · **added:** CI secret-scan job | C1 |
| R9 | Dependency abandonment / license change | 3 | 3 | 9 | ADR 0003, optional extras, pinned ruff · **added:** Dependabot | C1 |
| R10 | Untested sink paths (MySQL live, `.[all]` install) | 3 | 3 | 9 | unit tests · **added:** MySQL/MariaDB CI, `install-matrix` | C1 |
| R11 | Supply chain / provenance | 2 | 4 | 8 | secret scanning + push protection, CodeQL · **added:** every Action SHA-pinned, least-privilege workflow tokens, `pip-audit` over the locked set, license gate, CycloneDX SBOM + build-provenance/SBOM attestations, OpenSSF Scorecard | C1 |
| R12 | Scale / performance | 3 | 3 | 9 | batching, parallel downloads, 25 MB cap · **planned:** perf budget test, run report | C2/C3 |
| R13 | Release / rollback risk | 2 | 3 | 6 | tag-driven release, checksums, smoke test · **added:** rollback runbook and build-provenance/SBOM attestations | C1 |
| R14 | Optional AGPL dependency (`pdf` extra) creates license obligations | 3 | 3 | 9 | `pdf` is excluded from `[all]`; disclosed in README, docs/legal.md, and pyproject; **added:** license-scan CI gate (`scripts/check_licenses.py`) | C1 |
| R15 | Cross-sink semantic drift (collation, timestamp precision, eventual dedup) | 3 | 4 | 12 | idempotent upserts, no-clobber, per-sink counters · **added:** `document_sha256` on every sink, utf8mb4 tables, exact text ids, SQLite WAL + busy timeout, DuckDB conflict retry, ClickHouse `ReplacingMergeTree(updated_at)` + `FINAL`, Neo4j uniqueness constraints, UTC-aware timestamps · **added:** `--verify` semantic reconciliation (filing-id sets + `document_sha256`) | C1 |
| R16 | Non-reproducible CI installs | 3 | 3 | 9 | pinned ruff range · **added:** `uv.lock` with `uv lock --check` and `uv sync --frozen` in CI, container images pinned by digest | C1 |
| R17 | Repository controls weaker than assumed | 3 | 4 | 12 | branch protection, conversation resolution · **added:** strict up-to-date status checks, SHA-pinned Actions. Code-owner review and commit signatures deferred: a solo maintainer cannot satisfy a required approval without using the admin override, which bypasses every check | C1 |
| R18 | Broad PAT used as the wiki-mirror secret (blast radius, shared expiry) | 3 | 4 | 12 | secret scanning + push protection, workflow limited to `push`→`main`/dispatch, `contents: read`, `::add-mask::`, `wiki` environment gate · **added:** rotation runbook in SECURITY.md. A dedicated `repo`-only token is still recommended | C1 |

## High-impact controls (do these first)

1. **R1 — recorded HKEx fixtures + canary.** The only risk that can make the tool silently wrong.
2. **R4 — docs-consistency test.** Prevents the drift that already happened.
3. **R10 — MySQL live CI + `.[all]` install test.** Closes the two known verification holes.
4. **R5/R9/R11 — branch protection, Dependabot, CodeQL.** Cheap permanent floors.
5. **R2 — `--verify` reconciliation.** Turns "we hope the sinks agree" into a checked invariant.

## Success metrics (targets)

| Metric | Target |
|--------|--------|
| Register risks with an automated control | ≥ 80% |
| Sinks with live CI coverage | 8 / 8 |
| Docs-drift incidents after the guard | 0 |
| Time to detect an HKEx API break | < 24 h (canary) |
| Release rollback time | < 5 min (pin previous tag) |
| Open high-severity security alerts | 0 |
| Release artifacts with build provenance + SBOM | 100% (next release) |

## Ownership & cadence

Maintainer: Simon Mak. Review at each release (or quarterly if no release). New risks are
added with an L×I score and at least one control; a risk with no control is a release blocker.
