# nats-mcp

FastMCP Python MCP server for NATS messaging bus health. Gives agents read-only
access to server stats, connections, subscriptions, JetStream status, and health
via the NATS HTTP monitoring API.

## Tools

| Tool | Description |
|------|-------------|
| `get_server_stats` | Server stats: version, uptime, connections, messages, memory, CPU |
| `get_connections` | Active connections with subscription and message counts |
| `get_subscription_stats` | Subscription counts, cache hit rate, fanout stats |
| `get_jetstream_status` | JetStream streams, consumers, messages, bytes, API stats |
| `get_health` | Health check — ok or error |

## Configuration

| Variable | Default | Required | Purpose |
|----------|---------|----------|---------|
| `NATS_MONITOR_URL` | `http://localhost:8222` | No | NATS HTTP monitoring endpoint |
| `NATS_MCP_PORT` | (none) | No | Set to enable HTTP transport on `127.0.0.1:<port>`. Unset = stdio (default). |
| `NATS_MCP_API_TOKEN` | (none) | No | Bearer token for HTTP mode authentication. Uses `hmac.compare_digest()`. Only active when `NATS_MCP_PORT` is also set. |
| `LOG_LEVEL` | `INFO` | No | structlog log level |
| `LOG_FILE` | `/opt/appdata/nats-mcp/logs/nats-mcp.log` | No | Structured log output file |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (none) | No | OpenTelemetry tracing endpoint (opt-in) |

### Transport modes

**Stdio (default)** — when `NATS_MCP_PORT` is not set, the server runs in stdio mode for direct Claude Code / scoped-mcp subprocess usage.

**HTTP (streamable-http)** — when `NATS_MCP_PORT` is set, the server binds to `127.0.0.1:<port>` and serves MCP over streamable-http. If `NATS_MCP_API_TOKEN` is also set, all HTTP requests must include `Authorization: Bearer <token>`. A warning is logged if HTTP mode runs without a token.

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Running

```bash
# Stdio mode (default)
python -m nats_mcp.server

# HTTP mode with bearer auth
NATS_MCP_PORT=8494 NATS_MCP_API_TOKEN=your-token python -m nats_mcp.server

# Via PM2
pm2 start ecosystem.config.js
```

## Development

```bash
pip install -e ".[dev]"
pytest
pytest --cov=nats_mcp --cov-report=term-missing
ruff check .
ruff format .
```
