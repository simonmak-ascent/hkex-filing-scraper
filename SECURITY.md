# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 2.x (latest `main`) | Yes |
| 1.x | No |

Only the latest release on `main` receives security fixes. There are no backports.

## Reporting a vulnerability

**Do not open a public issue.** Email [simon.pl.mak@gmail.com](mailto:simon.pl.mak@gmail.com) with `SECURITY` in the
subject line, and include:

- the affected version or commit,
- a description of the issue and its impact,
- minimal reproduction steps or a proof of concept,
- any suggested remediation.

If you prefer, use GitHub's [private vulnerability reporting](https://github.com/simonmak-ascent/hkex-filing-scraper/security/advisories/new).

## What to expect

| Stage | Target |
| ----- | ------ |
| Acknowledgement of your report | 3 business days |
| Initial assessment and severity | 7 business days |
| Fix or documented mitigation | 30 days for high/critical, next release otherwise |
| Public disclosure | Coordinated with you after a fix ships |

We credit reporters in the release notes unless you ask to stay anonymous.

## Scope

In scope:

- The published package and its CLI (`hkex-scraper`).
- The read-only MCP servers: the stdio server (`hkex-scraper-mcp`) and the hosted live
  gateway (`https://hkex-listco-updates.ascent-partners.com/api/mcp`) and its
  documentation site.
- Credential handling — DSNs and passwords must never appear in logs, errors, or the SQL
  failure log (`sinks/base.py:redact()`, `db_postgres._redact()`).
- SQL construction — all values must be parameterised; `escape_sql()` is mandatory on every
  SurrealDB `/sql` path.
- The GitHub Actions workflows in `.github/workflows/`.

Out of scope:

- The undocumented HKEx JSON API and any HKEx-hosted service.
- Vulnerabilities in third-party database engines or drivers themselves — report those
  upstream. We will still bump a pinned dependency when a fix is available.
- Issues that require an already-compromised host, database, or credentials.
- Rate limiting, scraping etiquette, or terms-of-use questions — see [docs/legal.md](docs/legal.md).

## Safe harbor

We will not pursue legal action against researchers who make a good-faith effort to follow
this policy, avoid privacy violations and service disruption, and give us reasonable time to
respond before public disclosure.

## Hardening already in place

- Parameterised SQL everywhere; per-sink redaction of credentials in error paths.
- Secret scanning (with push protection), CodeQL, and Dependabot enabled on the repository.
- Every GitHub Action is pinned to a full commit SHA, so a moved tag cannot change what runs.
- Workflows declare least-privilege `permissions` (`contents: read` by default; each
  privileged job grants only what it needs).
- Releases are built in CI and carry **build provenance** and a **CycloneDX SBOM** attestation,
  verifiable with `gh attestation verify` (see [Releasing](docs/releasing.md)).
- Continuous dependency audit (`pip-audit` over the locked set), a license gate that fails on
  copyleft dependencies, and OpenSSF Scorecard.
- Container images used in CI and `examples/` are pinned by digest.

## Credential rotation

The wiki mirror reads one repository secret, `WIKI_TOKEN` (a classic PAT with `repo` scope,
required because fine-grained tokens cannot push to a `.wiki` repository). To rotate it:

1. Create a replacement **classic** PAT with the `repo` scope at
   <https://github.com/settings/tokens>.
2. Put it in the local, gitignored env file as `WIKI_TOKEN` (`~/.env.opencode`, mode `600`).
3. Update the repository secret without exposing the value:

   ```bash
   grep -E '^WIKI_TOKEN=' ~/.env.opencode | cut -d= -f2- | tr -d '\r\n' | gh secret set WIKI_TOKEN
   ```

4. Re-run the mirror: `gh workflow run wiki.yml --ref main`.
5. Revoke the previous token.

The `wiki` environment gates that secret: a run must be approved through the environment
before the token is exposed to a job. Any credential that has ever been pasted into a chat,
an issue, or a log must be treated as compromised and revoked immediately.
