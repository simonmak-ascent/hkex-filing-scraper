# Support

Thanks for using HKEx Filing Scraper. Here is where to go, depending on what you need.

## Documentation first

Most questions are answered in the docs:

- [Getting started](docs/getting-started.md) — install and run a first scrape.
- [Configuration reference](docs/configuration.md) — every environment variable.
- [CLI reference](docs/cli.md) — every flag.
- [Sink guides](docs/sinks/README.md) — per-engine setup, schema, and queries.
- [Troubleshooting](docs/troubleshooting.md) — common errors and fixes.
- [Docs site](https://hkex-listco-updates.ascent-partners.com/) — the same docs, searchable.

## Where to ask

| I want to… | Use |
| ---------- | --- |
| Ask a usage question, share a setup, or discuss ideas | [Discussions](https://github.com/simonmak-ascent/hkex-filing-scraper/discussions) |
| Report a reproducible bug | [Bug report](https://github.com/simonmak-ascent/hkex-filing-scraper/issues/new?template=bug_report.yml) |
| Request a feature or a new sink | [Feature request](https://github.com/simonmak-ascent/hkex-filing-scraper/issues/new?template=feature_request.yml) |
| Report a security vulnerability | [SECURITY.md](SECURITY.md) — **never** a public issue |
| Contribute code or docs | [CONTRIBUTING.md](CONTRIBUTING.md) |

## What to include in a bug report

To get a useful answer quickly, please include:

- the version (`hkex-scraper --version`) and Python version,
- the sink(s) in `DATABASE_TARGET`,
- the exact command you ran (redact credentials and DSNs),
- the full traceback or log excerpt,
- what you expected versus what happened.

Never paste real credentials, DSNs, or HKEx document contents into an issue.

## Scope of support

This is a volunteer, best-effort project. There is no commercial SLA. Security reports get a
defined response window — see [SECURITY.md](SECURITY.md) — but general issues are answered as
time allows. Bug reports with a clear reproduction are prioritised over open-ended questions.

## Commercial and data licensing

This is a research tool for the undocumented HKEx JSON API, and it is not affiliated with or
endorsed by HKEx. For questions about redistributing HKEx data, see [docs/legal.md](docs/legal.md).
