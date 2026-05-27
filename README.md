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

| Variable | Default | Required |
|----------|---------|----------|
| `NATS_MONITOR_URL` | `http://localhost:8222` | No |

No authentication required. The NATS monitoring port is open without credentials.

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Running

```bash
python -m nats_mcp.server
# or via PM2:
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
