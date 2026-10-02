# Governance

This document describes how the HKEx Filing Scraper project is run: who decides what, how
changes are reviewed, and how the project evolves.

## Model

The project is **maintainer-led**. It started as a personal research tool and is developed in
the open. There is currently one maintainer, who acts as the final decision maker on scope,
design, and releases.

| Role | Who | Responsibilities |
| ---- | --- | ---------------- |
| Maintainer | [@simonmak-ascent](https://github.com/simonmak-ascent) | Roadmap, architecture decisions, review, releases, security reports, repository administration. |
| Contributor | anyone | Issues, discussions, pull requests, docs, and sink adapters. |

As the contributor base grows, additional maintainers may be invited based on a sustained
record of high-quality contributions and demonstrated judgment.

## How decisions are made

- **Small changes** (bug fixes, docs, tests, a new sink adapter that follows the existing
  pattern) are decided in the pull request.
- **Significant changes** (architecture, public interfaces, a new dependency category, a
  breaking change) are recorded as an [Architecture Decision Record](docs/adr/) under `docs/adr/`
  before or alongside implementation. The ADR is the durable record of *why*.
- **Roadmap items** are tracked in [docs/roadmap.md](docs/roadmap.md) and [docs/de-risking.md](docs/de-risking.md).

Consensus is preferred; when consensus is not reached, the maintainer decides and documents
the reasoning in the relevant issue, PR, or ADR.

## Review and merge

- All changes land via pull request. `main` is protected: required status checks must pass and
  review conversations must be resolved before merge.
- At least one maintainer approval is required. The maintainer may merge their own pull
  requests when CI is green.
- Pull requests are expected to include tests for behavior changes and a `CHANGELOG.md` entry.
- Documentation drift is a CI failure, not a style preference — see
  [tests/test_docs_consistency.py](tests/test_docs_consistency.py).

## Releases

- Releases are cut from `main` by pushing an annotated tag (`vMAJOR.MINOR.PATCH`).
- The project follows [Semantic Versioning](https://semver.org/). The **sink list is part of
  the public interface**: adding a sink is a minor release; removing or renaming one is a major
  release.
- The release workflow builds, smoke-tests, and publishes the artifacts with checksums. See
  [docs/releasing.md](docs/releasing.md).

## Scope and support policy

The project supports open-source database engines; source-available engines (MongoDB, SurrealDB)
are included as labeled exceptions. The acceptance criteria for a new sink are defined in
[ADR 0003](docs/adr/0003-sink-support-policy.md).

## Code of conduct and security

- All participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
- Vulnerabilities are handled per the [Security Policy](SECURITY.md), never in public issues.

## Changing this document

Governance changes are proposed as a pull request that edits this file, giving contributors a
chance to comment before the maintainer merges it.
