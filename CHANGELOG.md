# Changelog

## [Unreleased]

## [0.2.0] — 2026-08-30

First tagged release. `[0.1.0]` and `[0.1.1]` below describe work that was never
cut as a release — the repo had no tags before this one.

### Added

- **`get_streams`** — per-stream inventory: `subjects`, message counts, first/last
  sequence, `consumer_count`, `retention`, `max_age`, `discard`.
- **`get_stream(name)`** — the full `config` and `state` blocks for one stream.
  Raises `NatsMcpError` for an unknown stream rather than returning `{}`.
- **`get_consumers(stream)`** — per-consumer lag: `num_pending`, `num_ack_pending`,
  `num_redelivered`, `delivered_stream_seq`, `ack_floor_stream_seq`. An empty list
  means the stream has no consumers; it is not an error.
- **`get_connections` gains `state`** — `"open"` (default), `"closed"` or `"all"`.
  Validated against that set before the request is issued.
- **`get_health` gains `js_enabled_only` and `js_server_only`**, mapping to
  `/healthz?js-enabled-only=` and `?js-server-only=`.
- **`NATS_MCP_HOST`** (default `127.0.0.1`) — HTTP bind address. `main()` previously
  hardcoded loopback, which is unreachable from a Docker network.
- **`NATS_MCP_ALLOW_NONLOOPBACK`** — explicit opt-in required for a non-loopback bind.
- **`nats_mcp/exceptions.py`** — `NatsMcpError` and the `NatsUnreachableError` /
  `NatsTimeoutError` / `NatsMonitoringError` subclasses.
- **OTEL is now actually wired.** A counter (`nats_mcp.tool.calls`) and a duration
  histogram (`nats_mcp.tool.duration`), both labelled `tool` and `outcome`, plus one
  span per tool call. Verified end to end against forge's SigNoz collector on
  2026-08-30 — spans in `signoz_traces`, metrics in `signoz_metrics`.
- `LICENSE` (MIT). `pyproject.toml` has declared `license = "MIT"` since 0.1.0 and
  the file never existed.
- README: the two mandatory badges, an architecture diagram, an observability
  section, and a worked `state="closed"` example.

### Changed

- **`get_connections` return shape** — added `ip`, `port`, `authorized_user`,
  `start`, `stop`, `reason`, `idle`, `uptime`. No keys removed. `stop` and `reason`
  exist only on closed connections and are carried through as `None` otherwise, so
  the key set does not vary by state. Taken as a breaking change now because the
  server is unwired and has no consumers.
- **`get_connections` always sends `auth=true`.** Without it NATS omits
  `authorized_user` from every connection object and still returns a complete,
  successful-looking 200 — the field that names the client simply is not there.
- Logging goes to **stdout**, not stderr, and writes **no file** unless `LOG_FILE`
  is explicitly set. The default was a hardcoded `/opt/appdata/nats-mcp/logs` path,
  so every run created a directory and an unrotated file. Rotation belongs to the
  deployment layer.
- Handlers attach to the named `nats-mcp` logger instead of the root logger, and
  `httpx`/`httpcore`/`mcp`/`fastmcp`/`uvicorn`/`starlette` are pinned to WARNING.
  Reconfiguring root captured and JSON-rendered every third-party record at the
  app's level (same pattern as vikunja#574, #552).
- `observability.py` is **in** the coverage gate. It was omitted, so none of the
  log-sink behaviour was ever measured. Only `__main__.py` remains omitted.
- CI installs `.[dev,otel]`. The OTEL tests `importorskip`, so a `[dev]`-only
  install skipped them while still reporting green.
- README's HTTP example port is **8508**, not 8494. Port 8494 belongs to
  `memsearch-summarize` in the services registry — the documented command failed on
  bind.

### Security

- **HTTP transport is now fail-closed.** It previously started and served every tool with
  no authentication when `NATS_MCP_API_TOKEN` was unset, emitting a single
  `nats_mcp_bearer_auth_disabled` warning — which nothing observes unless an operator is
  already tailing startup output. `main()` now raises when the token is missing or shorter
  than 16 characters, and when `NATS_MCP_HOST` is non-loopback without
  `NATS_MCP_ALLOW_NONLOOPBACK=1`. Matches backrest-mcp and scoped-mcp; the tools behind
  this port return client IP addresses and agent identities. Audit finding MEDIUM-1,
  2026-08-30. **stdio mode is unaffected** and still needs no token.

### Fixed

- **`get_jetstream_status.enabled` was unconditionally `True`.** It read
  `data.get("config") is not None or data.get("streams") is not None`; `streams` is
  `0`, not absent, when JetStream is on with no streams, and `/jsz` answers whenever
  the monitoring port does. It is now derived from `config.store_dir`. The old test
  only passed because its fixture used `streams: None`, which NATS never emits.
- **`get_connections` returned `None` for `subscriptions`, `msgs_to` and
  `msgs_from` against every real server.** It read `num_subs`, `msgs_to` and
  `msgs_from`, none of which are `/connz` fields; NATS spells them `subscriptions`,
  `out_msgs` and `in_msgs`. The test fixtures invented the same three key names, so
  the suite stayed green. Verified against NATS 2.12.6 across all three states.
- **`get_streams` sends `config=1`.** Without it `/jsz` omits the `config` block
  entirely, so `subjects`, `retention`, `max_age` and `discard` all come back `None`
  with no error — the same silent-omission shape as `auth=true`.
- **`get_server_stats` raised `TypeError` against every real NATS server** and had
  done since 0.1.0. `/varz` emits `uptime` as an already-formatted string
  (`"7h33m0s"`), and `_fmt_uptime` accepted only a nanosecond integer, so it
  evaluated `"7h33m0s" // 1_000_000_000`. Unnoticed because the server was never
  deployed and the fixture supplied an integer NATS does not emit. Strings now pass
  through; the nanosecond branch is kept for endpoints that do report in ns.
- A non-2xx from the monitoring API raised a raw `httpx.HTTPStatusError` at the MCP
  caller. `raise_for_status()` was called and nothing caught it. It is now
  `NatsMonitoringError`, carrying the status code and endpoint path.
- `AGENTS.md` claimed the server was deployed on forge as a PM2 process. It has
  never been deployed — no manifest references it and no PM2 app exists.
- `from __future__ import annotations` added to `__init__.py` and `__main__.py`.

## [0.1.1] — 2026-05-27

### Added

- `observability.py` — structured logging always on (stderr, JSON, structlog);
  default log path `/opt/appdata/nats-mcp/logs/nats-mcp.log`; log directory
  created at startup; OTEL tracing opt-in via `OTEL_EXPORTER_OTLP_ENDPOINT`.
- `configure_logging()` wired into `main()` before `mcp.run()`.
- `[otel]` optional dep group: `opentelemetry-sdk>=1.20`,
  `opentelemetry-exporter-otlp-proto-grpc>=1.20`.
- Bare `LOG_FILE` guard: `if log_dir:` check before `os.makedirs` prevents
  `FileNotFoundError` when `LOG_FILE` is set to a bare filename.

## [0.1.0] — 2026-05-27

### Added

- Initial release: FastMCP Python MCP server for NATS HTTP monitoring API
- 5 read-only tools: `get_server_stats`, `get_connections`, `get_subscription_stats`,
  `get_jetstream_status`, `get_health`
- NATS monitoring port 8222, no authentication required
- `get_connections`: `limit` typed as `int`, clamped to max 500
- `server_id` stripped from all responses (noise reduction)
- Human-readable uptime formatting (nanosecond integer → `2d 3h 15m 4s`)
- Clear error messages for connection refused and timeout conditions
- 10 tests with respx mocks — 92% coverage
- PM2 ecosystem config for forge deployment
