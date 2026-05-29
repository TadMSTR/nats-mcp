"""nats-mcp — FastMCP server for NATS monitoring API.

Read-only access to NATS server health, connections, subscriptions, JetStream
status, and overall health via the NATS HTTP monitoring API (port 8222).

Tools:
  get_server_stats       — Server stats: version, uptime, connections, messages
  get_connections        — Active connections with subscription and message counts
  get_subscription_stats — Subscription counts, cache hit rate, fanout stats
  get_jetstream_status   — JetStream streams, consumers, messages, bytes, API stats
  get_health             — Health check — ok or error

Configuration:
  NATS_MONITOR_URL — NATS HTTP monitoring base URL (default: http://localhost:8222)
"""

from __future__ import annotations

import hmac
import os
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

_log = structlog.get_logger("nats-mcp")

NATS_MONITOR_URL = os.environ.get("NATS_MONITOR_URL", "http://localhost:8222").rstrip("/")

_HTTP_TIMEOUT = 10.0
_MAX_CONNECTIONS_LIMIT = 500

mcp = FastMCP(
    name="nats",
    instructions=(
        "NATS MCP server. Read-only access to the NATS messaging bus on forge. "
        "Use get_server_stats for an overview of server health and traffic. "
        "Use get_connections to inspect active client connections. "
        "Use get_subscription_stats for subscription fanout and cache stats. "
        "Use get_jetstream_status for stream and consumer inventory. "
        "Use get_health for a simple ok/error health check. "
        "All tools are read-only — NATS client port is never used."
    ),
)


# ── HTTP helper ───────────────────────────────────────────────────────────────


async def _get(path: str, params: dict | None = None) -> dict:
    """GET a NATS monitoring endpoint and return the parsed JSON body."""
    url = f"{NATS_MONITOR_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError as exc:
        raise ConnectionError(f"NATS monitoring unreachable at {NATS_MONITOR_URL}") from exc
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"NATS monitoring did not respond within {_HTTP_TIMEOUT}s") from exc


def _fmt_uptime(ns: int | None) -> str:
    """Format NATS uptime (nanoseconds integer) as a human-readable string."""
    if ns is None:
        return "unknown"
    seconds = ns // 1_000_000_000
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
async def get_connections(limit: int = 100) -> dict:
    """Get active NATS client connections.

    Args:
        limit: Maximum connections to return (max 500).

    Returns:
        Dict with num_connections and a list of connection objects (cid, name,
        subscriptions, msgs_to, msgs_from, lang, version).
    """
    limit = min(limit, _MAX_CONNECTIONS_LIMIT)
    data = await _get("/connz", params={"limit": limit})
    connections = [
        {
            "cid": c.get("cid"),
            "name": c.get("name"),
            "subscriptions": c.get("num_subs"),
            "msgs_to": c.get("msgs_to"),
            "msgs_from": c.get("msgs_from"),
            "lang": c.get("lang"),
            "version": c.get("version"),
        }
        for c in data.get("connections", [])
    ]
    return {
        "num_connections": data.get("num_connections"),
        "connections": connections,
    }


@mcp.tool()
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
async def get_jetstream_status() -> dict:
    """Get JetStream status: streams, consumers, messages, bytes, and API stats.

    Returns:
        Dict with enabled flag, streams, consumers, messages, bytes, memory, storage,
        and api stats (total, errors).
    """
    data = await _get("/jsz")
    api = data.get("api", {})
    return {
        "enabled": data.get("config") is not None or data.get("streams") is not None,
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
async def get_health() -> dict:
    """Check NATS server health.

    Returns:
        Health status dict — {'status': 'ok'} or {'status': 'error', 'error': '...'}.
    """
    return await _get("/healthz")


class _BearerAuthMiddleware:
    """ASGI middleware that enforces static bearer token authentication.

    Only active when NATS_MCP_API_TOKEN is set in the environment.
    Requests missing a valid Authorization header receive a 401 response.
    Non-HTTP scopes (lifespan, websocket) are passed through unconditionally.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope, receive)
            auth_header = request.headers.get("authorization", "")
            provided = auth_header.removeprefix("Bearer ") if auth_header.lower().startswith("bearer ") else ""
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
    from .observability import configure_logging
    configure_logging()
    # Transport: stdio (default) or streamable-http when NATS_MCP_PORT is set.
    # Bearer auth (NATS_MCP_API_TOKEN) only applies to HTTP transport.
    port_env = os.environ.get("NATS_MCP_PORT")
    if port_env:
        port = int(port_env)
        api_token = os.environ.get("NATS_MCP_API_TOKEN")
        middleware: list[Any] = []
        if api_token:
            _log.info("nats_mcp_bearer_auth_enabled")
            middleware = [Middleware(_BearerAuthMiddleware, token=api_token)]
        else:
            _log.info("nats_mcp_bearer_auth_disabled", reason="NATS_MCP_API_TOKEN not set")
        mcp.run(transport="streamable-http", host="127.0.0.1", port=port, middleware=middleware or None)
    else:
        mcp.run()  # stdio — current default mode


if __name__ == "__main__":
    main()
