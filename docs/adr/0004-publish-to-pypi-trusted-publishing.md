# ADR 0004 — Publish to PyPI via Trusted Publishing

- Status: Accepted
- Date: 2026-09-17
- Deciders: maintainers

## Context

ADR 0001 deferred PyPI publishing and distributed the package GitHub-only (tags and release
assets). The package is also consumed as a pinned dependency by a web application, so
`pip install hkex-filing-scraper` from the public index is more convenient than a `git+`
URL or a downloaded wheel. Releasing to PyPI therefore moved from "nice to have" to
needed.

GitHub Packages was evaluated as an alternative PyPI-style registry. It does not offer a
Python/PyPI registry: its supported registries are Container, RubyGems, npm, Maven, Gradle,
and NuGet, and GitHub discontinued plans for Python package support. So GitHub Packages is
not a distribution channel for this project.

## Decision

- Publish to **PyPI** as `hkex-filing-scraper`.
- Use PyPI **Trusted Publishing** (OpenID Connect): the `.github/workflows/pypi.yml`
  workflow authenticates with a short-lived OIDC token issued to the repository's
  `pypi.yml` workflow, so **no long-lived upload token exists** anywhere to store, rotate, or
  leak.
- Keep GitHub Releases as a second, token-free channel (the `release.yml` workflow already
  attaches the wheel, sdist, SBOM, and checksums there).
- Do **not** use GitHub Packages: it has no Python registry.

## Alternatives considered

| Alternative | Why not chosen |
| ----------- | -------------- |
| Long-lived PyPI API token in a GitHub secret | A secret to store and rotate; already leaked once during setup. |
| GitHub Packages (PyPI) | Not supported — GitHub discontinued Python package support. |
| GitHub Releases only (status quo) | `pip install` requires a `git+` URL or manual wheel download; no public index. |

## Consequences

- Releases are published to PyPI and GitHub Releases from the same tag, with no upload
  token in either path.
- The PyPI trusted publisher (owner `simonmak-ascent`, repo `hkex-filing-scraper`,
  workflow `pypi.yml`) must be kept in sync if the repository is renamed or transferred.
- `pypi.yml` uses `skip-existing`, so re-dispatching a tag is idempotent.
- The distribution story is documented in `docs/releasing.md` and
  `docs/release-automation.md`.
