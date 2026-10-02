---
description: What's new in HKEx Filing Scraper — dated notes for each release, starting with the AI-agent MCP release.
---

# What's new

Short, dated notes for the things worth knowing. The full, itemised history is in the
[changelog](https://github.com/simonmak-ascent/hkex-filing-scraper/blob/main/CHANGELOG.md).

## v2.5.0 — sharper search across live HKEx filings

**27 September 2026**

The hosted [live MCP gateway](live-mcp.md) now narrows its live search four more ways and lets
you browse a window before drilling in.

- **Filters** — `search_filings` accepts `title_query`, `document_type`, `category`, and
  `stock_name` alongside `stock_code`, and the filters combine.
- **Facet browsing** — the new `list_filing_facets` tool lists the categories, document types,
  and stock codes present in a date window, with counts.
- **Richer results** — every filing now carries its file type, size, HKEx category, and news id.

## v2.4.0 — HKEx filings, live for AI agents

**20 September 2026**

You can now ask an AI agent about live HKEx filings — no install, no API key.

- **Hosted MCP gateway** at `https://hkex-listco-updates.ascent-partners.com/api/mcp`:
  Streamable HTTP, stateless, nothing stored, three read-only tools (`search_filings`,
  `get_filing`, `get_server_info`). See the [live MCP gateway](live-mcp.md).
- **AI agent support** for Claude Code and Claude Desktop, ChatGPT, Cursor, VS Code (GitHub
  Copilot), Gemini CLI, opencode, Manus, and Perplexity — copy-paste configuration in
  [AI agent support](ai-agents.md).
- **Published to the official MCP Registry** as
  `io.github.simonmak-ascent/hkex-filings`, which the third-party directories mirror.
- **Hardening** — protocol `2025-03-26` negotiation, empty capability lists for probing
  clients, desktop-shell origins, CORS preflight, and edge rate limiting.

## v2.3.0 — docs and the gateway share one deployment

**19 September 2026**

The documentation moved from GitHub Pages to
`https://hkex-listco-updates.ascent-partners.com/`, alongside the live MCP gateway.

## v2.2.0 — a read-only MCP server over your corpus

**19 September 2026**

`pip install "hkex-filing-scraper[mcp]"` provides `hkex-scraper-mcp`, a stdio MCP server with a
16-tool read-only catalog, plus composable search across every sink and optional PostgreSQL
trigram indexes. See the [MCP server](mcp.md).
