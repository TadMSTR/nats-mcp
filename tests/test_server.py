"""Tests for nats_mcp/server.py — NATS monitoring API tools."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

# ── get_server_stats ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_server_stats_happy_path():
    respx.get("http://localhost:8222/varz").mock(
        return_value=Response(
            200,
            json={
                "version": "2.12.6",
                "uptime": 3_661_000_000_000,  # 1h 1m 1s in nanoseconds
                "connections": 3,
                "total_connections": 42,
                "subscriptions": 10,
                "slow_consumers": 0,
                "mem": 12345678,
                "cpu": 0.5,
                "in_msgs": 1000,
                "out_msgs": 999,
                "in_bytes": 50000,
                "out_bytes": 49000,
                "server_id": "NAAAAAAABBBBBBBBCCCCCC",  # should not appear in output
            },
        )
    )
    from nats_mcp.server import get_server_stats

    result = await get_server_stats()
    assert result["version"] == "2.12.6"
    assert result["connections"] == 3
    assert result["uptime"] == "1h 1m 1s"
    assert "server_id" not in result


@pytest.mark.asyncio
@respx.mock
async def test_get_server_stats_uptime_days():
    respx.get("http://localhost:8222/varz").mock(
        return_value=Response(
            200,
            json={
                "version": "2.12.6",
                "uptime": 2 * 86400 * 1_000_000_000,  # 2 days exactly
                "connections": 0,
                "total_connections": 0,
                "subscriptions": 0,
                "slow_consumers": 0,
                "mem": 0,
                "cpu": 0,
                "in_msgs": 0,
                "out_msgs": 0,
                "in_bytes": 0,
                "out_bytes": 0,
            },
        )
    )
    from nats_mcp.server import get_server_stats

    result = await get_server_stats()
    assert result["uptime"] == "2d 0s"


# ── get_connections ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_connections_happy_path():
    respx.get("http://localhost:8222/connz").mock(
        return_value=Response(
            200,
            json={
                "num_connections": 2,
                "connections": [
                    {
                        "cid": 1,
                        "name": "agent-dev",
                        "num_subs": 3,
                        "msgs_to": 100,
                        "msgs_from": 50,
                        "lang": "python3",
                        "version": "2.3.0",
                        "server_id": "NAAAAAAABBBBBBBBCCCCCC",
                    },
                    {
                        "cid": 2,
                        "name": "agent-security",
                        "num_subs": 2,
                        "msgs_to": 20,
                        "msgs_from": 10,
                        "lang": "python3",
                        "version": "2.3.0",
                    },
                ],
            },
        )
    )
    from nats_mcp.server import get_connections

    result = await get_connections()
    assert result["num_connections"] == 2
    assert len(result["connections"]) == 2
    assert result["connections"][0]["name"] == "agent-dev"
    assert result["connections"][0]["subscriptions"] == 3
    # server_id must not appear in flattened output
    for conn in result["connections"]:
        assert "server_id" not in conn


@pytest.mark.asyncio
@respx.mock
async def test_get_connections_limit_capped():
    captured_params = []

    def capture(request):
        captured_params.append(dict(request.url.params))
        return Response(200, json={"num_connections": 0, "connections": []})

    respx.get("http://localhost:8222/connz").mock(side_effect=capture)
    from nats_mcp.server import get_connections

    await get_connections(limit=99999)
    assert int(captured_params[0]["limit"]) <= 500


# ── get_subscription_stats ────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_subscription_stats_happy_path():
    respx.get("http://localhost:8222/subsz").mock(
        return_value=Response(
            200,
            json={
                "num_subscriptions": 15,
                "num_cache": 8,
                "cache_hit_rate": 0.92,
                "max_fanout": 3,
                "avg_fanout": 1.2,
            },
        )
    )
    from nats_mcp.server import get_subscription_stats

    result = await get_subscription_stats()
    assert result["num_subscriptions"] == 15
    assert result["cache_hit_rate"] == 0.92


# ── get_jetstream_status ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_jetstream_status_happy_path():
    respx.get("http://localhost:8222/jsz").mock(
        return_value=Response(
            200,
            json={
                "config": {"max_memory": 1073741824},
                "streams": 2,
                "consumers": 5,
                "messages": 1000,
                "bytes": 50000,
                "memory": 2048,
                "storage": 10240,
                "api": {"total": 100, "errors": 0},
            },
        )
    )
    from nats_mcp.server import get_jetstream_status

    result = await get_jetstream_status()
    assert result["streams"] == 2
    assert result["consumers"] == 5
    assert result["api_total"] == 100
    assert result["api_errors"] == 0
    assert result["enabled"] is True


@pytest.mark.asyncio
@respx.mock
async def test_get_jetstream_status_disabled():
    respx.get("http://localhost:8222/jsz").mock(
        return_value=Response(
            200,
            json={
                "streams": None,
                "consumers": None,
                "messages": 0,
                "bytes": 0,
                "memory": 0,
                "storage": 0,
                "api": {"total": 0, "errors": 0},
            },
        )
    )
    from nats_mcp.server import get_jetstream_status

    result = await get_jetstream_status()
    assert result["enabled"] is False


# ── get_health ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_health_ok():
    respx.get("http://localhost:8222/healthz").mock(
        return_value=Response(200, json={"status": "ok"})
    )
    from nats_mcp.server import get_health

    result = await get_health()
    assert result["status"] == "ok"


# ── Connection refused ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_connection_refused_returns_clear_error():
    import httpx as _httpx

    respx.get("http://localhost:8222/varz").mock(
        side_effect=_httpx.ConnectError("connection refused")
    )
    from nats_mcp.server import get_server_stats

    with pytest.raises(ConnectionError) as exc_info:
        await get_server_stats()
    assert "NATS monitoring unreachable" in str(exc_info.value)
    assert "localhost:8222" in str(exc_info.value)


# ── Timeout ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_timeout_raises_timeout_error():
    import httpx as _httpx

    respx.get("http://localhost:8222/healthz").mock(
        side_effect=_httpx.TimeoutException("timed out")
    )
    from nats_mcp.server import get_health

    with pytest.raises(TimeoutError):
        await get_health()


# ---------------------------------------------------------------------------
# Bearer auth middleware tests
# ---------------------------------------------------------------------------


def test_bearer_auth_middleware_allows_valid_token():
    """_BearerAuthMiddleware passes through requests with a valid token."""
    import asyncio
    from nats_mcp.server import _BearerAuthMiddleware

    called = []

    async def fake_app(scope, receive, send):
        called.append(True)

    middleware = _BearerAuthMiddleware(fake_app, token="secret-token")

    async def run():
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer secret-token")],
            "method": "POST",
            "path": "/mcp",
            "query_string": b"",
        }
        await middleware(scope, None, lambda *a: None)

    asyncio.run(run())
    assert called, "Request with valid token should be passed through"


def test_bearer_auth_middleware_rejects_missing_token():
    """_BearerAuthMiddleware returns 401 when Authorization header is absent."""
    import asyncio
    from nats_mcp.server import _BearerAuthMiddleware

    responses = []

    async def fake_app(scope, receive, send):
        responses.append("app_called")

    async def capture_send(message):
        responses.append(message)

    middleware = _BearerAuthMiddleware(fake_app, token="secret-token")

    async def run():
        scope = {
            "type": "http",
            "headers": [],
            "method": "POST",
            "path": "/mcp",
            "query_string": b"",
        }
        await middleware(scope, None, capture_send)

    asyncio.run(run())
    assert "app_called" not in responses
    status_response = next((r for r in responses if r.get("type") == "http.response.start"), None)
    assert status_response is not None
    assert status_response["status"] == 401


def test_bearer_auth_middleware_passes_non_http_scope():
    """_BearerAuthMiddleware passes through non-HTTP (lifespan) scopes."""
    import asyncio
    from nats_mcp.server import _BearerAuthMiddleware

    called = []

    async def fake_app(scope, receive, send):
        called.append(scope["type"])

    middleware = _BearerAuthMiddleware(fake_app, token="secret-token")

    async def run():
        scope = {"type": "lifespan"}
        await middleware(scope, None, None)

    asyncio.run(run())
    assert called == ["lifespan"]
