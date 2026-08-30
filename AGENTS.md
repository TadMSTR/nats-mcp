---
owner: TadMSTR
github-account: personal
last-updated: 2026-08-30
---

# nats-mcp — Agent Instructions

## Purpose

FastMCP Python MCP server wrapping the NATS HTTP monitoring API with 8 read-only tools.
Gives agents direct visibility into NATS server health, client connections, subscriptions,
JetStream streams and consumers — without direct NATS client access.

**Not deployed.** This server appears in no agent manifest and has no PM2 app, and
nothing on forge runs it today. As of v0.3.0 the image and the compose fragment exist —
the **target** is a container named `nats-mcp` in the `nats` Docker stack on port 8508
(vikunja#496), reachable at `http://nats:8222` for monitoring. That is a target, not a
deployment: sysadmin merges `deploy/docker-compose.nats-mcp.yml` and deploys separately.
Do not restate this as deployed until that has happened. `ecosystem.config.js` documents
the PM2 install path and still works. From
2026-05-27 to 2026-08-30 this section asserted the opposite — a live PM2 deployment wired
into an agent's scoped-mcp config — which was never true at any point. Verified against
`/etc/forge/manifests/*.yml`, `~/.claude/manifests/` and the PM2 process list on
2026-08-30. Re-check before restating a deployment status here.

## Structure

```
nats_mcp/
  __init__.py         Package marker
  __main__.py         python -m nats_mcp entry point
  server.py           FastMCP server — all 8 tools, bearer-auth middleware, main()
  observability.py    structlog stdout logging + opt-in OTEL traces and metrics
  exceptions.py       NatsMcpError hierarchy
tests/
  __init__.py
  test_server.py         pytest + respx tests for all tools and main()
  test_observability.py  log sinks, OTEL gating, instrument()
deploy/
  docker-compose.nats-mcp.yml  Service FRAGMENT for ~/docker/nats/ — not a runnable stack
Dockerfile            Two-stage slim build, non-root 1000:1000, python HEALTHCHECK
.dockerignore         Does not inherit .gitignore — every entry is deliberate
ecosystem.config.js   PM2 stdio process config (install path, not currently running)
pyproject.toml        Package metadata, deps, ruff + pytest + coverage config
LICENSE               MIT
```

`deploy/` and the `Dockerfile` are both excluded from the image by `.dockerignore`: they
describe how to build and where to deploy, and have no business inside the artifact.

**Developer cannot write `~/docker`** (`/etc/forge/workspace-policy.yml` —
`write_roots: [~/repos/gitea, ~/repos/personal]`). That is why the compose service lives
in this repo as a fragment rather than being applied directly. Do not attempt to edit
`~/docker/nats/docker-compose.yml`; the write is blocked and the attempt is wasted work.

## Invariants

- All MCP tools are read-only — monitoring endpoints only, never the NATS client port.
- No authentication required — NATS monitoring port 8222 is open without credentials on forge,
  and is bound loopback-only. (`varz.http_host` reads `0.0.0.0`; that is the in-container
  bind, not the published binding. Do not "fix" it.)
- `limit` in `get_connections` is integer-typed and clamped; `state` is validated against
  `{open, closed, all}` before the request is issued. Nothing caller-supplied is
  string-interpolated into a URL.
- Response sizes are capped before returning to the MCP caller.
- No shell exec, subprocess calls, or filesystem writes. A log file is written only when
  `LOG_FILE` is explicitly set.
- **HTTP transport is fail-closed and must stay that way.** `main()` raises rather than
  starts when the token is missing or under 16 chars, or when the bind is non-loopback
  without `NATS_MCP_ALLOW_NONLOOPBACK`. Do not downgrade any of these to a warning — that
  was the pre-v0.2.0 behaviour and it is audit finding MEDIUM-1 (2026-08-30). A log line is
  not an access control: nothing observes it unless someone is already tailing startup
  output, and the process serves every tool in the meantime. Fleet pattern, matching
  backrest-mcp and scoped-mcp. stdio mode is exempt — it has no network surface.
- **`/health` is the only unauthenticated route, and the exemption is an exact path
  match.** `_AUTH_EXEMPT_PATHS` is a closed set checked with `not in`, never
  `path.startswith("/health")` — a prefix test would also hand `/healthz` and anything
  else added under that stem an unauthenticated pass. Its body is the literal
  `{"status": "ok"}`: do not add a version, a bind address or `NATS_MONITOR_URL`, because
  an unauthenticated route echoes to every co-tenant container on `forge-net`.
- **`/health` must not probe NATS.** It is liveness, not readiness. A dependency check
  there means a NATS restart marks the container unhealthy and Docker restarts a working
  process — a NATS blip traded for a nats-mcp outage. Add a separate `/ready` if a
  readiness signal is ever wanted. Note the `get_health` *tool* is the opposite direction
  and does query NATS `/healthz`; they are not interchangeable.
- `get_connections` discloses client IP addresses and `authorized_user` (an agent identity)
  to any caller holding the tool grant. That is deliberate — it is what makes an
  authorization-violation burst attributable — and it is why the containerised deploy's
  bearer token is mandatory, not optional.

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastmcp` | MCP server framework |
| `httpx` | Async HTTP client for NATS monitoring API calls |
| `structlog` | Structured JSON logging |
| `opentelemetry-sdk` + `-exporter-otlp-proto-grpc` | Optional (`[otel]` extra) — traces and metrics |

## Configuration

| Var | Default | Purpose |
|-----|---------|---------|
| `NATS_MONITOR_URL` | `http://localhost:8222` | NATS HTTP monitoring base URL |
| `NATS_MCP_PORT` | (unset) | Set → streamable-http transport. Unset → stdio |
| `NATS_MCP_HOST` | `127.0.0.1` | HTTP bind address; `0.0.0.0` in a container (needs the opt-in below) |
| `NATS_MCP_ALLOW_NONLOOPBACK` | (unset) | `1`/`true`/`yes` to permit a non-loopback bind |
| `NATS_MCP_API_TOKEN` | (unset) | Bearer token, min 16 chars. **Required** in HTTP mode |
| `LOG_LEVEL` | `INFO` | structlog level |
| `LOG_FILE` | (unset) | Extra file sink; unset = stdout only |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (unset) | OTLP gRPC endpoint — port 4317, not 4318 |

In the container these are set to `NATS_MONITOR_URL=http://nats:8222` (the service name on
`forge-net` — **never** `localhost`, which is the container's own loopback and the
vikunja#462 trap), `NATS_MCP_HOST=0.0.0.0` and `NATS_MCP_ALLOW_NONLOOPBACK=1` (both baked
into the image), `NATS_MCP_PORT=8508`, and
`OTEL_EXPORTER_OTLP_ENDPOINT=http://signoz-otel-collector:4317`. `LOG_FILE` stays unset —
that is what makes `read_only: true` viable with no writable path.

`NATS_MCP_API_TOKEN` is the only secret. It is never logged; the bearer comparison
uses `hmac.compare_digest()`.

## Extension points

- **Add new tools:** `nats_mcp/server.py` — follow the existing pattern, which is
  `@mcp.tool()` above `@instrument("<tool_name>")` above the function. Both decorators,
  in that order. Add corresponding tests.
- **Query parameters NATS omits silently:** two monitoring endpoints drop fields unless
  asked. `/connz` omits `authorized_user` without `auth=true`; `/jsz` omits the whole
  `config` block — and therefore `subjects`, `retention`, `max_age`, `discard` — without
  `config=1`. In both cases the response is a complete-looking 200 with the field simply
  absent. Any new endpoint parameter of this kind needs a test asserting the *request*
  carries it, not just that the response parses.
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
pip install -e ".[dev,otel]" ruff==0.16.0
pytest
pytest --cov=nats_mcp --cov-report=term-missing
```

Tests use `respx` to mock the NATS HTTP API. No real network calls. Coverage threshold: 80%
over `server.py`, `observability.py` and `exceptions.py`; only `__main__.py` is omitted.

Install the `otel` extra: the OTEL success-path tests `importorskip` without it, so a
`[dev]`-only install silently skips them and under-reports what is covered. CI installs
`.[dev,otel]`.

Fixtures mirror what NATS 2.12.6 actually emits. `/connz` connection objects use
`subscriptions`, `in_msgs` and `out_msgs` — **not** `num_subs`, `msgs_from`, `msgs_to`,
which are not /connz fields at all. Fixtures that invent field names keep the suite green
while the tool returns `None` against a live server; that is what happened here before
v0.2.0.

## Git workflow

Branch before editing — do not commit directly to `main`.
