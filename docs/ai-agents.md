---
description: Connect the live HKEx MCP gateway from Claude, ChatGPT, Cursor, VS Code/Copilot, Gemini CLI, opencode, Manus, and Perplexity.
---

# AI agent support

The [live MCP gateway](live-mcp.md) is a standard, public, read-only MCP server, so any
MCP-capable agent can connect to it. This page lists the popular clients, the exact
configuration each one needs, and what has been verified against the hosted endpoint.

The endpoint is:

```text
https://hkex-listco-updates.ascent-partners.com/api/mcp
```

There is **no authentication** and **no API key** — connect anonymously. The transport is
**Streamable HTTP** with JSON-RPC 2.0. A browser `GET` returns `405 Method Not Allowed` by
design (the endpoint offers no server-sent-events stream); if a client only offers an
"SSE" transport, choose "Streamable HTTP" where the client allows it, or bridge through
[`mcp-remote`](#stdio-only-clients).

## Compatibility

| Client | Transport | Status |
| ------ | --------- | ------ |
| opencode | Streamable HTTP (remote) | Verified live |
| Claude Code | Streamable HTTP (`--transport http`) | Supported |
| Claude Desktop | Remote connector, or `mcp-remote` stdio bridge | Supported |
| ChatGPT | Developer-mode custom connector | Supported |
| Cursor | Streamable HTTP (`url`) | Supported |
| VS Code (GitHub Copilot) | Streamable HTTP (`type: "http"`) | Supported |
| Gemini CLI | Streamable HTTP (`httpUrl`) | Supported |
| Manus | Custom MCP server (Streamable HTTP) | Supported |
| Perplexity | Custom remote connector (Streamable HTTP) | Supported |
| Any MCP SDK client | Streamable HTTP | Verified live (official Python SDK) |
| Any stdio-only client | `mcp-remote` bridge | Verified live |

"Verified live" means the handshake, `tools/list`, and a tool call were exercised against
the hosted endpoint. "Supported" clients speak the same standard Streamable HTTP
transport; only the configuration differs.

## Claude Code

```bash
claude mcp add --transport http hkex-live \
  https://hkex-listco-updates.ascent-partners.com/api/mcp
```

Or commit a project-scoped `.mcp.json` (a `url` entry **requires** a `type`):

```json
{
  "mcpServers": {
    "hkex-live": {
      "type": "http",
      "url": "https://hkex-listco-updates.ascent-partners.com/api/mcp"
    }
  }
}
```

## Claude Desktop

Add the server under **Settings → Connectors → Add custom connector** and paste the
endpoint URL. Alternatively, bridge it as a local stdio server in
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hkex-live": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://hkex-listco-updates.ascent-partners.com/api/mcp"]
    }
  }
}
```

## ChatGPT

Enable developer mode, then **Settings → Connectors → Advanced → Developer mode → Add
custom connector** and paste the endpoint URL as a remote MCP server.

## Cursor

Add to `~/.cursor/mcp.json` (or the project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "hkex-live": {
      "url": "https://hkex-listco-updates.ascent-partners.com/api/mcp"
    }
  }
}
```

## VS Code and GitHub Copilot

Add to `.vscode/mcp.json` (workspace) or the user profile `mcp.json`:

```json
{
  "servers": {
    "hkex-live": {
      "type": "http",
      "url": "https://hkex-listco-updates.ascent-partners.com/api/mcp"
    }
  }
}
```

## Gemini CLI

Add to `~/.gemini/settings.json` (Streamable HTTP uses `httpUrl`; `url` is the deprecated
SSE form):

```json
{
  "mcpServers": {
    "hkex-live": {
      "httpUrl": "https://hkex-listco-updates.ascent-partners.com/api/mcp"
    }
  }
}
```

## opencode

Add to `opencode.json` (global or project):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "hkex-live": {
      "type": "remote",
      "url": "https://hkex-listco-updates.ascent-partners.com/api/mcp",
      "enabled": true
    }
  }
}
```

## Manus

In Manus, open **Settings → Integrations → Custom MCP Servers → Add Server**, then provide
the server name, the endpoint URL, and the transport (**Streamable HTTP**). Manus verifies
the connection and lists the tools.

## Perplexity

In Perplexity, open the connector settings and choose **Add custom remote connector**, then
set the URL to the endpoint, the transport to **Streamable HTTP**, and the authentication
to **None**.

## stdio-only clients

If a client can only launch local `stdio` servers, bridge the gateway with
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote):

```json
{
  "mcpServers": {
    "hkex-live": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://hkex-listco-updates.ascent-partners.com/api/mcp"]
    }
  }
}
```

For a stored corpus (rather than live fetches), use the [stdio MCP server](mcp.md)
instead, which reads from a configured database sink.

## Protocol details

- **Transport** — Streamable HTTP, `POST` with JSON-RPC 2.0; `OPTIONS` answers CORS
  preflight; other methods return `405` with `Allow: POST`.
- **Protocol versions** — `2024-11-05`, `2025-03-26`, `2025-06-18`, and `2025-11-25` are
  negotiated with the version the client requests; anything else falls back to
  `2024-11-05`.
- **Capabilities** — tools only. Probes for `resources/list`, `resources/templates/list`,
  and `prompts/list` return empty lists so probing clients connect cleanly.
- **Origin** — a web `Origin` must be allowlisted (MCP's DNS-rebinding mitigation).
  Desktop shells that send no `Origin`, `null`, or a `file://` origin are allowed.
- **Tools** — `get_server_info`, `search_filings`, `list_filing_facets`, and `get_filing`; all read-only.

## See also

- [Live MCP gateway](live-mcp.md) — tools, limits, and security model.
- [MCP server](mcp.md) — the stdio server over a stored corpus.
