"""nats-mcp — FastMCP server for NATS monitoring API.

Read-only access to NATS server health, connections, subscriptions, JetStream
streams and consumers via the NATS HTTP monitoring API (port 8222). The NATS
client port (4222) is never touched.

Tools:
  get_server_stats       — Server stats: version, uptime, connections, messages
  get_connections        — Client connections, open or closed, with identity and IP
  get_subscription_stats — Subscription counts, cache hit rate, fanout stats
  get_jetstream_status   — JetStream account-wide totals and API stats
  get_streams            — Per-stream inventory: subjects, message counts, sequences
  get_stream             — Full config and state for one stream
  get_consumers          — Per-consumer lag: pending, ack-pending, redelivered
  get_health             — Health check — ok or error

Configuration:
  NATS_MONITOR_URL — NATS HTTP monitoring base URL (default: http://localhost:8222)
  NATS_MCP_PORT    — set to serve streamable-http instead of stdio
  NATS_MCP_HOST    — HTTP bind address (default: 127.0.0.1)
  NATS_MCP_API_TOKEN — bearer token, HTTP transport only. REQUIRED in HTTP mode.
  NATS_MCP_ALLOW_NONLOOPBACK — opt in to a non-loopback bind
"""

from __future__ import annotations

import atexit
import hmac
import os
from collections.abc import Iterator
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .exceptions import (
    NatsMcpError,
    NatsMonitoringError,
    NatsTimeoutError,
    NatsUnreachableError,
)
from .observability import instrument

_log = structlog.get_logger("nats-mcp")

NATS_MONITOR_URL = os.environ.get("NATS_MONITOR_URL", "http://localhost:8222").rstrip("/")

_HTTP_TIMEOUT = 10.0
_MAX_CONNECTIONS_LIMIT = 500

# /connz accepts exactly these three states. A caller-supplied value is validated
# against this set before it reaches the query string — never interpolated raw.
_CONNZ_STATES = frozenset({"open", "closed", "all"})

# HTTP transport adds a network surface stdio never had, and the tools behind it
# return client IP addresses and agent identities. Fail closed: a bearer token is
# mandatory, and a non-loopback bind needs an explicit opt-in. Matches the fleet
# pattern in backrest-mcp and scoped-mcp; searxng-mcp's 2026-08-19 containerise is
# on record as the counter-example, where optional off-by-default auth removed the
# only access control the moment it moved into a container.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MIN_API_TOKEN_LENGTH = 16

# The only paths that answer without a bearer token. Exact match against
# scope['path'], never a prefix test: startswith('/health') would also exempt
# /healthz, /health-debug and anything else someone adds later under that stem.
# This is a closed list of one entry, not a namespace.
_AUTH_EXEMPT_PATHS = frozenset({"/health"})

mcp = FastMCP(
    name="nats",
    instructions=(
        "NATS MCP server. Read-only access to the NATS messaging bus on forge. "
        "Use get_server_stats for an overview of server health and traffic. "
        "Use get_connections to inspect client connections — pass state='closed' to see "
        "why connections dropped, including authorization violations and the client identity. "
        "Use get_subscription_stats for subscription fanout and cache stats. "
        "Use get_jetstream_status for JetStream account totals. "
        "Use get_streams, get_stream and get_consumers for per-stream inventory and "
        "consumer lag. "
        "Use get_health for a simple ok/error health check. "
        "All tools are read-only — NATS client port is never used."
    ),
)


# ── HTTP helper ───────────────────────────────────────────────────────────────


async def _get(path: str, params: dict | None = None) -> dict:
    """GET a NATS monitoring endpoint and return the parsed JSON body.

    Raises:
        NatsUnreachableError: the monitoring API could not be contacted.
        NatsTimeoutError: the monitoring API did not respond within the timeout.
        NatsMonitoringError: the monitoring API returned a non-2xx status.
    """
    url = f"{NATS_MONITOR_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError as exc:
        raise NatsUnreachableError(f"NATS monitoring unreachable at {NATS_MONITOR_URL}") from exc
    except httpx.TimeoutException as exc:
        raise NatsTimeoutError(f"NATS monitoring did not respond within {_HTTP_TIMEOUT}s") from exc
    except httpx.HTTPStatusError as exc:
        # raise_for_status() was already being called, but nothing caught it — a
        # 503 from a starting server reached the MCP caller as a raw httpx
        # exception with the full request URL in its repr.
        raise NatsMonitoringError(exc.response.status_code, path) from exc


async def _jsz_account_details() -> list[dict]:
    """Fetch /jsz with per-stream detail and return the account_details list.

    ``config=1`` is not optional. Without it the ``config`` block — which is where
    ``subjects``, ``retention``, ``max_age`` and ``discard`` live — is absent from
    every stream_detail entry, and the response still looks entirely successful.
    Same silent-omission shape as ``auth=true`` on /connz. Verified against NATS
    2.12.6 on 2026-08-30; ``tests/test_server.py`` asserts the parameter is sent.
    """
    data = await _get("/jsz", params={"streams": 1, "consumers": 1, "config": 1})
    return data.get("account_details") or []


def _iter_stream_details(account_details: list[dict]) -> Iterator[dict]:
    """Yield every stream_detail entry across every account."""
    for account in account_details:
        yield from account.get("stream_detail") or []


def _project_stream(stream: dict) -> dict:
    """Flatten one /jsz stream_detail entry into the get_streams summary shape."""
    config = stream.get("config") or {}
    state = stream.get("state") or {}
    return {
        "name": stream.get("name"),
        "subjects": config.get("subjects"),
        "messages": state.get("messages"),
        "bytes": state.get("bytes"),
        "first_seq": state.get("first_seq"),
        "last_seq": state.get("last_seq"),
        "consumer_count": state.get("consumer_count"),
        "retention": config.get("retention"),
        "max_age": config.get("max_age"),
        "discard": config.get("discard"),
    }


def _fmt_uptime(value: int | str | None) -> str:
    """Format a NATS uptime value as a human-readable string.

    /varz emits `uptime` as an ALREADY-FORMATTED string ("7h33m0s") — verified
    against NATS 2.12.6 on 2026-08-30. This function previously accepted only a
    nanosecond integer and did `value // 1_000_000_000`, so get_server_stats
    raised TypeError on every call against a real server and had done since
    0.1.0. It went unnoticed because the server was never deployed and the test
    fixture supplied an integer NATS does not emit.

    The integer branch is kept: /connz and some other endpoints do report
    durations in nanoseconds, and a future NATS could change /varz back.
    """
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value
    seconds = value // 1_000_000_000
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
@instrument("get_server_stats")
async def get_server_stats() -> dict:
    """Get NATS server statistics: version, uptime, connections, messages, memory, CPU.

    Returns:
        Dict with version, uptime (human-readable), connections, total_connections,
        subscriptions, slow_consumers, mem, cpu, in_msgs, out_msgs, in_bytes, out_bytes.
    """
    data = await _get("/varz")
    return {
        "version": data.get("version"),
        "uptime": _fmt_uptime(data.get("uptime")),
        "connections": data.get("connections"),
        "total_connections": data.get("total_connections"),
        "subscriptions": data.get("subscriptions"),
        "slow_consumers": data.get("slow_consumers"),
        "mem": data.get("mem"),
        "cpu": data.get("cpu"),
        "in_msgs": data.get("in_msgs"),
        "out_msgs": data.get("out_msgs"),
        "in_bytes": data.get("in_bytes"),
        "out_bytes": data.get("out_bytes"),
    }


@mcp.tool()
@instrument("get_connections")
async def get_connections(limit: int = 100, state: str = "open") -> dict:
    """Get NATS client connections — who is connected, or who was disconnected and why.

    Args:
        limit: Maximum connections to return (max 500).
        state: Which connections to return — "open" (default), "closed", or "all".
            Closed connections carry `stop` and `reason`; `reason` reads
            "Authorization Violation" for a failed authentication.

    Returns:
        Dict with num_connections and a list of connection objects (cid, name,
        ip, port, authorized_user, subscriptions, msgs_to, msgs_from, lang,
        version, start, stop, reason, idle, uptime).

    Raises:
        NatsMcpError: `state` is not one of open/closed/all.
    """
    if state not in _CONNZ_STATES:
        raise NatsMcpError(f"invalid state {state!r} — expected one of {sorted(_CONNZ_STATES)}")
    limit = min(limit, _MAX_CONNECTIONS_LIMIT)
    # auth=true is mandatory: without it `authorized_user` is absent from every
    # connection object and the response still looks completely successful. That
    # field is the only thing on /connz that names the client, and naming the
    # client is the entire reason this tool exists (vikunja#425/#529/#574).
    data = await _get("/connz", params={"limit": limit, "state": state, "auth": "true"})
    connections = [
        {
            "cid": c.get("cid"),
            "name": c.get("name"),
            "ip": c.get("ip"),
            "port": c.get("port"),
            "authorized_user": c.get("authorized_user"),
            # NATS names these `subscriptions`/`in_msgs`/`out_msgs`. This tool
            # previously read `num_subs`/`msgs_to`/`msgs_from`, which are not
            # /connz fields — every row returned None for all three, and the test
            # fixtures invented the same three keys so the suite stayed green.
            # Verified against NATS 2.12.6, all three states, 2026-08-30.
            "subscriptions": c.get("subscriptions"),
            "msgs_to": c.get("out_msgs"),
            "msgs_from": c.get("in_msgs"),
            "lang": c.get("lang"),
            "version": c.get("version"),
            "start": c.get("start"),
            # Present only on closed connections. Carried through as None rather
            # than omitted so the key set does not vary by state.
            "stop": c.get("stop"),
            "reason": c.get("reason"),
            "idle": c.get("idle"),
            "uptime": c.get("uptime"),
        }
        for c in data.get("connections", [])
    ]
    return {
        "num_connections": data.get("num_connections"),
        "connections": connections,
    }


@mcp.tool()
@instrument("get_subscription_stats")
async def get_subscription_stats() -> dict:
    """Get NATS subscription statistics: counts, cache hit rate, fanout stats.

    Returns:
        Dict with num_subscriptions, num_cache, cache_hit_rate, max_fanout, avg_fanout.
    """
    data = await _get("/subsz")
    return {
        "num_subscriptions": data.get("num_subscriptions"),
        "num_cache": data.get("num_cache"),
        "cache_hit_rate": data.get("cache_hit_rate"),
        "max_fanout": data.get("max_fanout"),
        "avg_fanout": data.get("avg_fanout"),
    }


@mcp.tool()
@instrument("get_jetstream_status")
async def get_jetstream_status() -> dict:
    """Get JetStream account-wide totals: stream/consumer counts, bytes, and API stats.

    This is the overview. For per-stream subjects and sequences use get_streams;
    for consumer lag use get_consumers.

    Returns:
        Dict with enabled flag, streams, consumers, messages, bytes, memory, storage,
        and api stats (total, errors).
    """
    data = await _get("/jsz")
    api = data.get("api", {})
    config = data.get("config") or {}
    return {
        # Derived from config.store_dir, which /jsz only emits when JetStream is
        # actually enabled. The previous expression was
        #   data.get("config") is not None or data.get("streams") is not None
        # which is unconditionally True: `streams` is 0 (not absent) when
        # JetStream is on with no streams, and /jsz answers whenever the
        # monitoring port answers at all. It could never report disabled.
        "enabled": bool(config.get("store_dir")),
        "streams": data.get("streams"),
        "consumers": data.get("consumers"),
        "messages": data.get("messages"),
        "bytes": data.get("bytes"),
        "memory": data.get("memory"),
        "storage": data.get("storage"),
        "api_total": api.get("total"),
        "api_errors": api.get("errors"),
    }


@mcp.tool()
@instrument("get_streams")
async def get_streams() -> dict:
    """List every JetStream stream with its subjects, message counts and sequences.

    Answers "which streams exist and what do they actually carry?" — the question
    behind an agent publishing into a subject no stream is bound to (vikunja#556,
    #562).

    Returns:
        Dict with num_streams and a list of stream objects (name, subjects,
        messages, bytes, first_seq, last_seq, consumer_count, retention, max_age,
        discard).
    """
    streams = [_project_stream(sd) for sd in _iter_stream_details(await _jsz_account_details())]
    return {"num_streams": len(streams), "streams": streams}


@mcp.tool()
@instrument("get_stream")
async def get_stream(name: str) -> dict:
    """Get the full config and state for one JetStream stream.

    Args:
        name: Stream name, e.g. "AGENT_BUS". Case-sensitive, as NATS stores it.

    Returns:
        Dict with name, created, config (the complete stream config block) and
        state (the complete stream state block).

    Raises:
        NatsMcpError: no stream with that name exists.
    """
    for sd in _iter_stream_details(await _jsz_account_details()):
        if sd.get("name") == name:
            return {
                "name": sd.get("name"),
                "created": sd.get("created"),
                "config": sd.get("config") or {},
                "state": sd.get("state") or {},
            }
    # An empty dict would read as "the stream exists and is empty", which is a
    # different fact and the wrong one to act on.
    raise NatsMcpError(f"stream {name!r} not found")


@mcp.tool()
@instrument("get_consumers")
async def get_consumers(stream: str) -> dict:
    """Get consumer lag for one JetStream stream.

    num_pending is the backlog; num_ack_pending is in flight and unacknowledged;
    num_redelivered counts messages a consumer has failed to ack at least once.

    Args:
        stream: Stream name, e.g. "AGENT_BUS".

    Returns:
        Dict with stream, num_consumers and a list of consumer objects (name,
        num_pending, num_ack_pending, num_redelivered, delivered_stream_seq,
        ack_floor_stream_seq). An empty list means the stream genuinely has no
        consumers — it is not an error.

    Raises:
        NatsMcpError: no stream with that name exists.
    """
    for sd in _iter_stream_details(await _jsz_account_details()):
        if sd.get("name") != stream:
            continue
        consumers = [
            {
                "name": c.get("name"),
                "num_pending": c.get("num_pending"),
                "num_ack_pending": c.get("num_ack_pending"),
                "num_redelivered": c.get("num_redelivered"),
                "delivered_stream_seq": (c.get("delivered") or {}).get("stream_seq"),
                "ack_floor_stream_seq": (c.get("ack_floor") or {}).get("stream_seq"),
            }
            for c in sd.get("consumer_detail") or []
        ]
        return {"stream": stream, "num_consumers": len(consumers), "consumers": consumers}
    raise NatsMcpError(f"stream {stream!r} not found")


@mcp.tool()
@instrument("get_health")
async def get_health(js_enabled_only: bool = False, js_server_only: bool = False) -> dict:
    """Check NATS server health.

    Args:
        js_enabled_only: Report healthy as soon as JetStream is enabled, without
            waiting for every stream and consumer to be current.
        js_server_only: Check only that the JetStream server is up, skipping
            per-account asset checks.

    Returns:
        Health status dict — {'status': 'ok'} or {'status': 'error', 'error': '...'}.
    """
    params: dict[str, str] = {}
    if js_enabled_only:
        params["js-enabled-only"] = "true"
    if js_server_only:
        params["js-server-only"] = "true"
    return await _get("/healthz", params=params or None)


@mcp.custom_route("/health", methods=["GET"])
async def liveness(_request: Request) -> JSONResponse:
    """Liveness probe for the container HEALTHCHECK. Unauthenticated by design.

    Distinct from the ``get_health`` *tool*, which queries the NATS ``/healthz``
    endpoint. This is the reverse direction: it answers for *this process* and
    deliberately does not touch NATS at all.

    That is the whole design. A readiness-style probe that reached upstream would
    mark this container unhealthy whenever NATS restarted, and compose would then
    restart a process that was working perfectly — trading a NATS blip for a
    nats-mcp outage. This server is stateless and reconnects per request, so it
    needs no restart to recover. If a readiness signal is ever wanted, add a
    separate ``/ready``; do not overload liveness with a dependency check.

    Unauthenticated means the body is public. It carries a literal status and
    nothing else: no version, no bind address, no ``NATS_MONITOR_URL``. This is the
    one route on the server that answers without a token, so anything echoed here
    is echoed to anyone on forge-net.
    """
    return JSONResponse({"status": "ok"})


class _BearerAuthMiddleware:
    """ASGI middleware that enforces static bearer token authentication.

    Only active when NATS_MCP_API_TOKEN is set in the environment.
    Requests missing a valid Authorization header receive a 401 response.
    Non-HTTP scopes (lifespan, websocket) are passed through unconditionally,
    as are the paths in _AUTH_EXEMPT_PATHS — currently /health alone, which the
    container HEALTHCHECK calls before it could possibly hold a token.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") not in _AUTH_EXEMPT_PATHS:
            request = Request(scope, receive)
            auth_header = request.headers.get("authorization", "")
            provided = (
                auth_header.removeprefix("Bearer ")
                if auth_header.lower().startswith("bearer ")
                else ""
            )
            if not hmac.compare_digest(provided, self._token):
                response = Response(
                    content='{"error":"Unauthorized"}',
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def main() -> None:
    from .observability import configure_logging, get_tracer, shutdown_observability

    configure_logging()
    # Initialise OTEL up front rather than lazily on the first tool call, so a
    # misconfigured endpoint warns at startup instead of on whatever call happens
    # to be first. Returns None and stays silent when the env var is unset.
    if get_tracer() is not None:
        _log.info("nats_mcp_otel_enabled")
    atexit.register(shutdown_observability)
    # Transport: stdio (default) or streamable-http when NATS_MCP_PORT is set.
    # Bearer auth (NATS_MCP_API_TOKEN) only applies to HTTP transport.
    port_env = os.environ.get("NATS_MCP_PORT")
    if port_env:
        port = int(port_env)
        api_token = os.environ.get("NATS_MCP_API_TOKEN")
        # Default stays loopback so the stdio/PM2 path is unchanged. A container
        # needs 0.0.0.0: inside a network namespace the bind address is not the
        # security control — the compose `ports:` publish is — and binding the
        # container's own loopback makes the server unreachable from forge-net.
        # That is exactly why it must be opted into rather than merely warned about.
        host = os.environ.get("NATS_MCP_HOST", "127.0.0.1")
        allow_nonloopback = os.environ.get("NATS_MCP_ALLOW_NONLOOPBACK", "").lower() in (
            "1",
            "true",
            "yes",
        )

        # These three refuse rather than warn. A log line is not an access control:
        # nothing sees it unless someone is already tailing startup output, and the
        # process serves every tool in the meantime (audit 2026-08-30, MEDIUM-1).
        if host not in _LOOPBACK_HOSTS and not allow_nonloopback:
            raise RuntimeError(
                f"Refusing to bind nats-mcp HTTP transport to non-loopback host {host!r}. "
                "Set NATS_MCP_ALLOW_NONLOOPBACK=1 to override."
            )
        if not api_token:
            raise RuntimeError(
                "Refusing to start nats-mcp HTTP transport without NATS_MCP_API_TOKEN set. "
                "These tools return client IP addresses and authorized_user identities; "
                "HTTP mode must not run with an unauthenticated, reachable port."
            )
        if len(api_token) < _MIN_API_TOKEN_LENGTH:
            raise RuntimeError(
                f"NATS_MCP_API_TOKEN is too short ({len(api_token)} chars, need "
                f">= {_MIN_API_TOKEN_LENGTH}). "
                'Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))"'
            )

        middleware: list[Any] = [Middleware(_BearerAuthMiddleware, token=api_token)]
        _log.info("nats_mcp_http_transport", host=host, port=port)
        mcp.run(transport="streamable-http", host=host, port=port, middleware=middleware)
    else:
        mcp.run()  # stdio — current default mode


if __name__ == "__main__":  # pragma: no cover
    main()
