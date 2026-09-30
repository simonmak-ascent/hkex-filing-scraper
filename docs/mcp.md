---
description: Run the read-only stdio MCP server over a stored HKEx corpus and query it from an LLM client.
---

# MCP server

The package ships an optional [Model Context Protocol](https://modelcontextprotocol.io)
(MCP) server so an LLM client can read a scraped HKEx corpus directly. It exposes a
fixed catalog of **read-only** tools over **stdio** and speaks only to the configured
database sinks — it never scrapes the network, never writes a record, and never runs
schema DDL.

To query HKEx live without a database, see the [live MCP gateway](live-mcp.md) instead.

A hosted copy of this stdio server (built from this repository's Dockerfile) is published on
[Glama](https://glama.ai/mcp/servers/simonplmak-cloud/hkex-filing-scraper), where Glama scans the
built server and scores tool-definition quality.

```mermaid
flowchart LR
    A["AI agent<br/>any MCP host"] -->|stdio| S["hkex-scraper-mcp<br/>read-only · 17 tools"]
    S -->|"SQL reads"| D["Your database<br/>nine sinks"]
```

## Install

```bash
pip install "hkex-filing-scraper[mcp]"
```

`mcp` is also included in the `all` extra. It is not a base dependency: `import
hkex_scraper` never requires it.

## Run

The server reads configuration from the **current working directory** exactly as the CLI
does — `Path.cwd()/.env`, not the repository root. Start it from the directory that holds
your `.env` and `DATABASE_TARGET`:

```bash
hkex-scraper-mcp
```

It speaks MCP over stdin/stdout, so it is normally launched by a client rather than by
hand. Example client configuration:

```jsonc
{
  "mcpServers": {
    "hkex-filings": {
      "command": "hkex-scraper-mcp",
      "env": {
        "DATABASE_TARGET": "postgres",
        "POSTGRES_DSN": "postgresql://reader:password@localhost:5432/hkex"
      }
    }
  }
}
```

Reads are served by the first configured sink whose capabilities include `reads`
(`DATABASE_TARGET` order). See [Database sinks](sinks/README.md) for the capability
matrix and [Configuration](configuration.md) for every variable.

## Tools

All tools are annotated `readOnlyHint: true`, `destructiveHint: false`, and
`openWorldHint: false`.

| Tool | Returns |
| ---- | ------- |
| `get_server_info` | Server version, configured sinks, and the read sink. |
| `list_sinks` | Every sink id with license, extra, configured/available status, and capabilities. |
| `get_config` | `DATABASE_TARGET`, sink order, read sink, and graph settings (never credentials). |
| `describe_schema` | Canonical filing/document field names, enums, and the query dimensions. |
| `list_companies` | `view="companies"` (default) lists companies (ticker + name) with filing counts; `view="tickers"` lists the paged, sorted distinct tickers. |
| `search_filings` | Filings filtered by ticker, stock code, title, type, category, status, exchange, referenced ticker, or date range. |
| `search_documents` | Full-text search over extracted `document_text`, with a snippet when the sink supports it. |
| `get_statistics` | Filing counts grouped by ticker, type, category, status, or exchange. |
| `list_pending_filings` | Filings by document-processing status (default `unprocessed`). |
| `get_filing` | One filing's metadata plus extracted document text and tables. |
| `get_filings` | Several filings in one call (up to 50 ids). |
| `get_coverage` | Per-chunk scrape coverage, with a date filter and totals. |
| `verify_sinks` | Cross-sink check (two or more sinks): `mode="hashes"` compares filing ids and document hashes, `mode="counts"` compares per-sink counts and the spread. |
| `list_references` | Graph edges for a company: `referenced_by` (filings that mention it) or `owned` (its own filings). |

List results carry an explicit completeness envelope — `returned_count`, `total_count`,
`has_more`, and `next_offset` — so a client can tell a full page from a partial one.

## Querying filings

`search_filings` accepts optional, combinable filters. Comma-separate a value to match
several (for example `filing_type="Annual Report,Dividend"`):

- `ticker`, `stock_code`, `referenced_ticker`
- `title_query` (case-insensitive title substring)
- `filing_type`, `filing_category`
- `document_status` (a real status, or `unprocessed` for a filing with no document yet)
- `exchange`, `source`, `document_type`
- `date_from` / `date_to` (`YYYY-MM-DD`, inclusive)

Results are ordered by `filing_date` descending by default; set `order_by` to
`filing_date_asc`, `title_asc`, or `filing_id_asc` to change that. Each row carries the
full filing metadata plus a document summary (`document_status`, `document_type`,
`document_text_len`, `document_table_cnt`) — never the document text itself.

`search_documents` runs the same filters plus a `text_query` matched case-insensitively
against extracted document text, and adds a `snippet` when the sink supports snippets
(reported as `snippets_supported`). It returns nothing until documents are processed.

Substring search is a portable ``LIKE`` predicate. On PostgreSQL, `POSTGRES_FTS_INDEX`
(default on) creates optional `pg_trgm` GIN indexes over `lower(title)` and
`lower(document_text)` so the planner can use an index bitmap scan instead of a full
scan; a database user without privilege logs a warning and search still works. The other
sinks remain scan-based for now.

`get_statistics(group_by=...)` counts filings by `company_ticker` (default),
`filing_type`, `filing_category`, `document_status`, `exchange`, or `sink` (per-sink
totals across configured sinks), under the same filters.

## Reading a filing

`get_filing` returns the canonical filing and document fields. `document_text` is
returned as a window so a large filing never floods the model context:

- `max_text_chars` (default 20000, maximum 200000) bounds the window; `text_offset`
  starts it.
- `text_truncated` and `next_text_offset` say whether more remains and how to continue.
- Tables (`document_tables`) are omitted unless `include_tables` is true.

`get_filings` reads several filings in one call (up to 50 ids); text is off by default,
and ids that do not exist are listed under `not_found`.

Three MCP resources are also exposed for out-of-band retrieval: `hkex://schema`,
`hkex://coverage`, and `hkex://filing/{filing_id}`.

## Safety

- **Reads only.** No tool writes, and the server never calls `ensure_schema()`.
- **Point it at a read-only identity.** The database adapters do not enforce read-only
  access themselves, so grant the MCP server a read-only database user (or a replica).
  This is the strongest control; the `readOnlyHint` annotations are hints, not
  enforcement.
- **Bounded reads.** Page sizes, ticker and coverage limits, and document text windows
  are capped server-side; the model cannot raise them.
- **Redacted errors.** Driver errors are returned with credentials scrubbed, and appear
  as MCP tool errors (`isError`) with an actionable message.
