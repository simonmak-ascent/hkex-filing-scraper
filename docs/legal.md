# Legal, Terms of Use, and Data

This project is a **research and data-engineering tool**. It is not affiliated with, endorsed
by, or sponsored by Hong Kong Exchanges and Clearing Limited (HKEX).

## What this project is

- Software that queries a publicly reachable, **undocumented** HKEx JSON endpoint and stores
  the results in a database **you** control.
- Distributed under the MIT license (`LICENSE`).

## What this project is not

- It does **not** ship, host, or redistribute any HKEx data. The repository contains no
  filings, no extracted text, and no datasets — only code and tests.
- It does not provide legal advice or a data license.

## Your responsibilities

1. **Terms of Use.** Access to HKEx websites and services is governed by the HKEX Terms of
   Use. In particular, automated collection and **commercial redistribution** of HKEx data may
   require a licensed HKEx feed or written permission. Review the current terms before
   commercial use: <https://www.hkex.com.hk/Global/Exchange/Terms-of-Use>.
2. **Rate limiting.** Be a good citizen: keep concurrency modest, use the built-in batching,
   and do not hammer the endpoint. The scraper defaults are chosen to be polite
   (`MAX_DOWNLOAD_WORKERS`, monthly chunking, and `REQUEST_DELAY_SECONDS` for pacing).
3. **Personal data & sensitive content.** Filings can contain personal data (e.g. director
   names). If you process them, comply with the laws that apply to you (for example, the
   Hong Kong Personal Data (Privacy) Ordinance and the GDPR where relevant).
4. **Attribution.** HKEx is the source of the underlying filings. Do not present the data as
   your own, and retain provenance (`source = 'HKEx'`, `documentUrl`, `filingId`).

## License of this software

MIT — see [LICENSE](https://github.com/simonmak-ascent/hkex-filing-scraper/blob/main/LICENSE). The MIT license covers **this code only**, not any HKEx data
you collect with it.

## Third-party licenses

The base install and `.[all]` use permissively licensed dependencies only. One optional extra
is different:

| Extra | Dependency | License | Notes |
| ----- | ---------- | ------- | ----- |
| `pdf` | PyMuPDF, pymupdf4llm | **AGPL-3.0** (or commercial via Artifex) | Not included in `.[all]`. The AGPL's network clause applies if you offer the software as a service. |
| `pdf` | camelot-py | MIT | Its optional Ghostscript backend is AGPL; the default backend ships as a wheel. |
| `excel` | openpyxl | MIT | |
| sink drivers | `psycopg`, `PyMySQL`, `duckdb`, `pymongo`, `clickhouse-connect`, `neo4j` | permissive/OSI | See each project. |

Database engines themselves are separate programs under their own licenses (for example,
MongoDB is SSPL and SurrealDB is BSL 1.1 — source-available, not OSI-approved). Connecting to
an engine does not change this project's license, but running the engine is governed by its
own terms.

## Contact

Questions about licensing this project's code: open an issue. Questions about HKEx data
licensing: contact HKEX directly.
