# Changelog

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
