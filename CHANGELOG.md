# Changelog

## [Unreleased]

### Added

- Standard CI workflow (`ci.yml`) — ruff (pinned `ruff==0.16.0`) + pytest + pip-audit + build.
- Release workflow (`release.yml`) — a `vX.Y.Z` tag cuts a source-only GitHub Release.
- Coverage config: exclude `observability.py` and `__main__.py` (boilerplate/entry-point)
  from the 80% gate so it measures real logic (`server.py`, 84%).
- **HTTP transport mode** — opt-in via `NATS_MCP_PORT` env var. Runs streamable-http
  on `127.0.0.1:<port>` instead of stdio. Bearer token auth (`NATS_MCP_API_TOKEN`)
  available in HTTP mode using `hmac.compare_digest()`. Logs warning when HTTP mode
  runs without a token configured.

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
