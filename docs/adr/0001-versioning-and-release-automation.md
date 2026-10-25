---
description: "ADR 0001: use hatch-vcs so the git tag is the single source of truth for the version, and let CI build and publish from that tag. Superseded by ADR 0004."
---

# ADR 0001 — Versioning and release automation

- Status: Superseded by ADR 0004 (PyPI publishing)
- Date: 2026-09-16
- Deciders: maintainers

## Context

The project was previously released by hand: edit a version string, build locally, and
attach files to a GitHub Release manually. The version existed in two places
(`pyproject.toml` and `src/hkex_scraper/__init__.py`), which can drift from the git tag,
and the build was not reproducible from the repository alone.

The package is also consumed as a pinned dependency by a web application, so released
versions must be stable, predictable, and verifiable.

## Decision

- Use **`hatch-vcs`** so the **git tag is the single source of truth** for the version.
  The version file (`src/hkex_scraper/_version.py`) is generated at build time and
  git-ignored; `__init__.py` falls back to `0.0.0+unknown` for an unbuilt checkout.
- Releases are triggered by **pushing a `v*` tag** and produced by
  `.github/workflows/release.yml`, which builds an sdist and wheel, verifies the wheel
  version equals the tag, smoke-tests the wheel in a clean environment, writes
  `SHA256SUMS`, and attaches everything to the GitHub Release.
- Distribution is **GitHub-only** (no PyPI); consumers install from a tag or a release
  asset.

## Alternatives considered

| Alternative | Why not chosen |
| ----------- | -------------- |
| Static version in `pyproject.toml` + `importlib.metadata` | Requires a manual bump and a CI check to stay aligned with the tag; easy to forget. |
| Hatch path source pointing at `__init__.py` | Same manual-sync problem; also couples `__init__` to the build. |
| `bump-my-version` | An editing/release helper, not a build-time source of truth; can leave partial bumps. |
| Publish to PyPI | Deferred; the project is distributed via GitHub for now. |

## Consequences

- Maintainers only create a tag; there is no second version to update.
- Tagged builds are reproducible (`@vX.Y.Z` pins an exact snapshot; assets carry checksums).
- Untagged builds carry development versions and must never be published as releases.
- The release workflow is the single place that can publish; if it breaks, the manual
  fallback in `docs/release-automation.md` documents an equivalent local procedure.
- Moving to PyPI later is additive: the same build outputs can be uploaded there.
