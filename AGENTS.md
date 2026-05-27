---
owner: TadMSTR
github-account: personal
last-updated: 2026-05-27
---

# nats-mcp — Agent Instructions

## Purpose

FastMCP Python MCP server wrapping the NATS HTTP monitoring API with 5 read-only tools.
Gives agents direct visibility into NATS server health, connections, subscriptions,
JetStream status, and overall health — without direct NATS client access.
Deployed on forge as a PM2 process wired into the sysadmin agent's scoped-mcp config.

## Structure

```
nats_mcp/
  __init__.py     Package marker
  __main__.py     python -m nats_mcp entry point
  server.py       FastMCP server — all 5 tools
tests/
  __init__.py
  test_server.py  pytest + respx tests for all tools
ecosystem.config.js   PM2 stdio process config
pyproject.toml        Package metadata, deps, ruff + pytest config
```

## Invariants

- All MCP tools are read-only — monitoring endpoints only, never the NATS client port.
- No authentication required — NATS monitoring port 8222 is open without credentials on forge.
- `limit` parameter in `get_connections` is integer-typed and clamped, never string-interpolated into URLs.
- Response sizes are capped before returning to the MCP caller.
- No shell exec, subprocess calls, or filesystem writes.

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastmcp` | MCP server framework |
| `httpx` | Async HTTP client for NATS monitoring API calls |
| `structlog` | Structured JSON logging |

## Configuration

| Var | Default | Purpose |
|-----|---------|---------|
| `NATS_MONITOR_URL` | `http://localhost:8222` | NATS HTTP monitoring base URL |

No secrets required.

## Extension points

- **Add new tools:** `nats_mcp/server.py` — follow existing `@mcp.tool()` pattern; add corresponding tests
- **Do not expose:** NATS client port (4222) — monitoring port (8222) only

## Out of scope for agents

- Implementing any write or publish operations against NATS
- Exposing any NATS client port endpoints
- Adding authentication without an explicit security review

## Security notes

- Only the NATS HTTP monitoring API (port 8222) is used — never the client port (4222)
- `limit` in `get_connections` is typed as `int` in the function signature, not interpolated as a string
- Connection refused errors return a clear message without internal path details

## Testing

```bash
pip install -e ".[dev]"
pytest
pytest --cov=nats_mcp --cov-report=term-missing
```

Tests use `respx` to mock the NATS HTTP API. No real network calls. Coverage threshold: 80%.

## Git workflow

Branch before editing — do not commit directly to `main`.
