# Contributing

Thanks for taking the time to contribute. This project follows a [Code of Conduct](CODE_OF_CONDUCT.md) — by participating you agree to uphold it.

## Ways to contribute

- **Report a bug** — open an issue with the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml).
- **Request a feature** — open an issue with the [feature request template](.github/ISSUE_TEMPLATE/feature_request.yml).
- **Add a sink** — adapters are small; start with [Adding a sink](docs/sinks/README.md#adding-a-sink) and [ADR 0003](docs/adr/0003-sink-support-policy.md).
- **Improve the docs** — see [docs/STYLE.md](docs/STYLE.md) for the house style.
- **Ask a question** — use [Discussions](https://github.com/simonmak-ascent/hkex-filing-scraper/discussions), or see [SUPPORT.md](SUPPORT.md).

## Development setup

```bash
git clone https://github.com/simonmak-ascent/hkex-filing-scraper.git
cd hkex-filing-scraper
pip install -e ".[dev,all]"
```

`.[dev]` is enough for the unit suite. `.[all]` adds the document-extraction libraries and
every database driver, which the optional integration tests need.

## Development workflow

1. Create a feature branch from `main` (`feat/…`, `fix/…`, `docs/…`, `chore/…`).
2. Make the change, following the conventions below.
3. Run the checks locally:

   ```bash
   ruff check && ruff format --check
   pytest -q
   ```

4. Open a pull request against `main` and fill in the template.

Tests are pure unit tests and need no database or network. SQLite and DuckDB integration
tests run in-process on every `pytest`; server-backed sinks (PostgreSQL, MySQL/MariaDB,
MongoDB, ClickHouse, Neo4j, SurrealDB) are skipped unless the matching connection variables
are set — see [docs/testing.md](docs/testing.md).

## Conventions

- **Python 3.10+**, `ruff` linted (`py310`, line length 100) and `ruff format`ed.
- **Parameterised SQL only.** Never interpolate values into a statement; `escape_sql()` is
  mandatory on every SurrealDB `/sql` path. See [constitution.md](constitution.md).
- **Never log credentials.** Route error text through `sinks/base.py:redact()`.
- **Optional dependencies stay optional** — guard each driver import with an `_AVAILABLE`
  flag and degrade gracefully.
- **A new sink field must be mirrored** across the SurrealDB schema, the PostgreSQL schema,
  the dialect column DDL, and the MongoDB/Neo4j/ClickHouse field lists; the
  [docs consistency test](tests/test_docs_consistency.py) enforces that the docs, templates,
  and `.env.example` stay in step.
- **Docs** follow [docs/STYLE.md](docs/STYLE.md) (US English, sentence-case headings, the term
  "sink" for a configured destination).
- **Coverage ratchets.** `[tool.coverage.report].fail_under` in `pyproject.toml` is a floor
  that only ever rises. The number is measured over the unit suite, which cannot execute the
  live-only modules (sink drivers, extractor, SurrealDB/PostgreSQL/Neo4j/MongoDB/ClickHouse
  adapters) — those are covered by the integration CI jobs. Add tests, then raise the floor in
  the same pull request.
- **Tests are offline by default.** `tests/conftest.py` blocks non-loopback connections, so a
  test that reaches the network fails with an explanatory message; mark a genuinely networked
  test `@pytest.mark.network` (excluded from the default run).

## Commit message conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` a new feature
- `fix:` a bug fix
- `docs:` documentation only
- `chore:` maintenance, tooling, dependencies
- `refactor:` a change that neither fixes a bug nor adds a feature
- `test:` tests only

Write the subject in the imperative mood (`add ClickHouse sink`, not `added`). Keep it under
72 characters; put the why in the body.

## Brand assets

The visual identity is the docs theme: amber on near-black, monospace. The palette lives in
[docs/assets/stylesheets/terminal.css](docs/assets/stylesheets/terminal.css); keep new artwork
on it.

| Asset | Role |
| ----- | ---- |
| `docs/assets/social.svg` → `docs/social_preview.png` | README hero, `og:image`, GitHub social preview (1280×640) |
| `docs/assets/mcp.svg` → `docs/assets/mcp.png` | MCP architecture diagram (README + PyPI) |
| `docs/assets/banner.svg` | Docs home hero |
| `docs/assets/favicon.svg` | Site logo and favicon |
| `docs/assets/demo.svg` | Terminal demo in the README |

The PNGs are generated from the SVGs with headless Chromium, because it resolves the embedded
CSS and system fonts exactly as a browser does:

```bash
python scripts/render_brand_assets.py           # re-render the PNGs
python scripts/render_brand_assets.py --check   # verify committed sizes (used by tests)
```

Chromium is found automatically (Playwright's bundled copy, `/usr/bin/chromium`, or
`google-chrome`); set `CHROME_BIN` to override.

**When `docs/social_preview.png` changes, refresh the repository social preview** — GitHub has
no API for it. Open **Settings → General → Social preview → Edit → Upload an image** and
upload the 1280×640 PNG. Otherwise shared repository links keep the old card.

## Releasing

A release is created by pushing an annotated tag. The **Release** workflow builds the
distribution, runs the smoke test, and attaches the wheel, sdist, and `SHA256SUMS` to the
GitHub Release.

```bash
git tag -a v2.0.1 -m "v2.0.1"
git push origin v2.0.1
```

- Step-by-step guide: [docs/releasing.md](docs/releasing.md)
- Full reference: [docs/release-automation.md](docs/release-automation.md)

## Questions

Open an issue, or start a [Discussion](https://github.com/simonmak-ascent/hkex-filing-scraper/discussions). For security issues, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
