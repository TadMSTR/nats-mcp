"""Tests for nats_mcp/server.py — NATS monitoring API tools."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from nats_mcp.exceptions import (
    NatsMcpError,
    NatsMonitoringError,
    NatsTimeoutError,
    NatsUnreachableError,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────
#
# Every connection object below uses the field names NATS 2.12.6 actually emits,
# captured from the live server on 2026-08-30. The names matter: the previous
# fixtures invented `num_subs`/`msgs_to`/`msgs_from`, which are not /connz fields
# at all, so the suite stayed green while the tool returned None for all three
# against a real server.

_OPEN_CONNZ = {
    "num_connections": 2,
    "connections": [
        {
            "cid": 1,
            "name": "agent-dev",
            "ip": "172.20.1.23",
            "port": 33782,
            "authorized_user": "agent-dev",
            "subscriptions": 3,
            "out_msgs": 100,
            "in_msgs": 50,
            "lang": "python3",
            "version": "2.3.0",
            "start": "2026-08-30T06:09:19Z",
            "idle": "1m0s",
            "uptime": "7h14m49s",
            "server_id": "NAAAAAAABBBBBBBBCCCCCC",
        },
        {
            "cid": 2,
            "name": "agent-security",
            "ip": "172.20.1.24",
            "port": 33999,
            "authorized_user": "agent-security",
            "subscriptions": 2,
            "out_msgs": 20,
            "in_msgs": 10,
            "lang": "python3",
            "version": "2.3.0",
            "start": "2026-08-30T06:10:00Z",
            "idle": "2m0s",
            "uptime": "7h0m0s",
        },
    ],
}

# A closed connection dropped for a failed authentication — the exact shape behind
# vikunja#425 ("13.9M failed connections, client unidentified"), #529 and #574.
_CLOSED_CONNZ = {
    "num_connections": 1,
    "connections": [
        {
            "cid": 8,
            "name": "githost-mcp-steward",
            "ip": "172.20.1.52",
            "port": 58946,
            "authorized_user": "githost-mcp-steward",
            "subscriptions": 0,
            "out_msgs": 0,
            "in_msgs": 0,
            "lang": "nats.js",
            "version": "3.4.0",
            "start": "2026-08-30T06:04:04Z",
            "stop": "2026-08-30T06:09:06Z",
            "reason": "Authorization Violation",
            "idle": "5m1s",
            "uptime": "5m1s",
        }
    ],
}

# /jsz?streams=1&consumers=1&config=1 — note the `config` block, which is present
# ONLY because config=1 was sent. Without it NATS omits it entirely and `subjects`
# silently disappears.
_JSZ_STREAMS = {
    "config": {"store_dir": "/data/jetstream"},
    "streams": 1,
    "consumers": 1,
    "messages": 68,
    "bytes": 43996,
    "api": {"total": 0, "errors": 0},
    "account_details": [
        {
            "name": "$G",
            "stream_detail": [
                {
                    "name": "AGENT_BUS",
                    "created": "2026-08-29T00:14:02Z",
                    "config": {
                        "name": "AGENT_BUS",
                        "subjects": ["events.agent-bus.>"],
                        "retention": "limits",
                        "max_age": 2592000000000000,
                        "discard": "old",
                        "storage": "file",
                    },
                    "state": {
                        "messages": 68,
                        "bytes": 43996,
                        "first_seq": 1,
                        "last_seq": 68,
                        "num_subjects": 2,
                        "consumer_count": 1,
                    },
                    "consumer_detail": [
                        {
                            "name": "bus-reader",
                            "num_pending": 12,
                            "num_ack_pending": 3,
                            "num_redelivered": 1,
                            "delivered": {"consumer_seq": 56, "stream_seq": 56},
                            "ack_floor": {"consumer_seq": 53, "stream_seq": 53},
                        }
                    ],
                }
            ],
        }
    ],
}

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
    respx.get("http://localhost:8222/connz").mock(return_value=Response(200, json=_OPEN_CONNZ))
    from nats_mcp.server import get_connections

    result = await get_connections()
    assert result["num_connections"] == 2
    assert len(result["connections"]) == 2
    first = result["connections"][0]
    assert first["name"] == "agent-dev"
    # Sourced from the NATS field names, not the invented num_subs/msgs_to/
    # msgs_from the previous fixture used. Asserting the *values* — a None here
    # is exactly the regression this catches.
    assert first["subscriptions"] == 3
    assert first["msgs_to"] == 100  # NATS out_msgs — sent to the client
    assert first["msgs_from"] == 50  # NATS in_msgs — received from the client
    assert first["ip"] == "172.20.1.23"
    assert first["authorized_user"] == "agent-dev"
    # Open connections have no stop/reason, but the keys are still present so the
    # shape does not vary by state.
    assert first["stop"] is None
    assert first["reason"] is None
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
                "config": {"store_dir": "/data/jetstream", "max_memory": 1073741824},
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
    # A REAL JetStream-disabled /jsz body: `streams` is 0, not None, and there is
    # no `config` block. The previous fixture used `streams: None`, which NATS
    # never emits — the old expression
    #     data.get("config") is not None or data.get("streams") is not None
    # returns True against this realistic body, so `enabled` could never report
    # disabled on a live server. Verified 2026-08-30.
    respx.get("http://localhost:8222/jsz").mock(
        return_value=Response(
            200,
            json={
                "streams": 0,
                "consumers": 0,
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


@pytest.mark.asyncio
@respx.mock
async def test_get_jetstream_status_enabled_with_zero_streams():
    """JetStream on but empty must still report enabled — the other side of the bug."""
    respx.get("http://localhost:8222/jsz").mock(
        return_value=Response(
            200,
            json={
                "config": {"store_dir": "/data/jetstream", "max_memory": 1073741824},
                "streams": 0,
                "consumers": 0,
                "messages": 0,
                "bytes": 0,
                "memory": 0,
                "storage": 0,
                "api": {"total": 0, "errors": 0},
            },
        )
    )
    from nats_mcp.server import get_jetstream_status

    assert (await get_jetstream_status())["enabled"] is True


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

    with pytest.raises(NatsUnreachableError) as exc_info:
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

    with pytest.raises(NatsTimeoutError):
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


# ── get_connections — state validation and identity fields ────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_connections_sends_auth_true():
    """auth=true must be on the wire.

    Without it NATS omits `authorized_user` from every connection object and still
    returns 200 with an otherwise complete body — the failure looks exactly like
    success. Verified against a live server with a negative control on 2026-08-30.
    This asserts the request, not the response, because a fixture can hand back
    `authorized_user` regardless of what was actually sent.
    """
    captured = []

    def capture(request):
        captured.append(dict(request.url.params))
        return Response(200, json={"num_connections": 0, "connections": []})

    respx.get("http://localhost:8222/connz").mock(side_effect=capture)
    from nats_mcp.server import get_connections

    await get_connections()
    assert captured[0]["auth"] == "true"


@pytest.mark.asyncio
@respx.mock
async def test_get_connections_sends_state():
    captured = []

    def capture(request):
        captured.append(dict(request.url.params))
        return Response(200, json={"num_connections": 0, "connections": []})

    respx.get("http://localhost:8222/connz").mock(side_effect=capture)
    from nats_mcp.server import get_connections

    await get_connections(state="closed")
    assert captured[0]["state"] == "closed"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("bad_state", ["'; DROP", "nope", "OPEN", "", "open closed"])
async def test_get_connections_rejects_invalid_state(bad_state):
    """An invalid state is rejected before any request is issued."""
    route = respx.get("http://localhost:8222/connz").mock(
        return_value=Response(200, json={"num_connections": 0, "connections": []})
    )
    from nats_mcp.server import get_connections

    with pytest.raises(NatsMcpError):
        await get_connections(state=bad_state)
    # The point is not just that it raised — it must not have reached the network.
    assert not route.called


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("good_state", ["open", "closed", "all"])
async def test_get_connections_accepts_valid_states(good_state):
    respx.get("http://localhost:8222/connz").mock(
        return_value=Response(200, json={"num_connections": 0, "connections": []})
    )
    from nats_mcp.server import get_connections

    assert (await get_connections(state=good_state))["num_connections"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_get_connections_closed_surfaces_auth_violation():
    """The #425/#529/#574 question: who failed to authenticate, and from where?"""
    respx.get("http://localhost:8222/connz").mock(return_value=Response(200, json=_CLOSED_CONNZ))
    from nats_mcp.server import get_connections

    conn = (await get_connections(state="closed"))["connections"][0]
    assert conn["reason"] == "Authorization Violation"
    assert conn["authorized_user"] == "githost-mcp-steward"
    assert conn["ip"] == "172.20.1.52"
    assert conn["stop"] == "2026-08-30T06:09:06Z"


@pytest.mark.asyncio
@respx.mock
async def test_get_connections_key_set_is_stable_across_states():
    """Open and closed rows carry the same keys; stop/reason are None, not absent."""
    from nats_mcp.server import get_connections

    respx.get("http://localhost:8222/connz").mock(return_value=Response(200, json=_OPEN_CONNZ))
    open_keys = set((await get_connections(state="open"))["connections"][0])
    respx.get("http://localhost:8222/connz").mock(return_value=Response(200, json=_CLOSED_CONNZ))
    closed_keys = set((await get_connections(state="closed"))["connections"][0])
    assert open_keys == closed_keys
    assert {"stop", "reason", "authorized_user", "ip"} <= open_keys


# ── get_streams / get_stream / get_consumers ─────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_streams_sends_config_param():
    """config=1 must be on the wire.

    Same silent-omission shape as auth=true: without config=1 NATS returns 200
    with every stream_detail entry present but no `config` block, so `subjects`,
    `retention`, `max_age` and `discard` all come back None and nothing signals
    that anything went wrong. Verified against NATS 2.12.6 on 2026-08-30.
    """
    captured = []

    def capture(request):
        captured.append(dict(request.url.params))
        return Response(200, json={"account_details": []})

    respx.get("http://localhost:8222/jsz").mock(side_effect=capture)
    from nats_mcp.server import get_streams

    await get_streams()
    assert captured[0]["config"] == "1"
    assert captured[0]["streams"] == "1"
    assert captured[0]["consumers"] == "1"


@pytest.mark.asyncio
@respx.mock
async def test_get_streams_happy_path():
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json=_JSZ_STREAMS))
    from nats_mcp.server import get_streams

    result = await get_streams()
    assert result["num_streams"] == 1
    stream = result["streams"][0]
    assert stream["name"] == "AGENT_BUS"
    assert stream["subjects"] == ["events.agent-bus.>"]
    assert stream["messages"] == 68
    assert stream["first_seq"] == 1
    assert stream["last_seq"] == 68
    assert stream["consumer_count"] == 1
    assert stream["retention"] == "limits"
    assert stream["max_age"] == 2592000000000000
    assert stream["discard"] == "old"


@pytest.mark.asyncio
@respx.mock
async def test_get_streams_empty_when_no_streams():
    respx.get("http://localhost:8222/jsz").mock(
        return_value=Response(200, json={"streams": 0, "account_details": []})
    )
    from nats_mcp.server import get_streams

    assert await get_streams() == {"num_streams": 0, "streams": []}


@pytest.mark.asyncio
@respx.mock
async def test_get_streams_handles_missing_account_details():
    """JetStream off: /jsz answers 200 with no account_details key at all."""
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json={"streams": 0}))
    from nats_mcp.server import get_streams

    assert (await get_streams())["num_streams"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_get_stream_returns_full_config_and_state():
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json=_JSZ_STREAMS))
    from nats_mcp.server import get_stream

    result = await get_stream("AGENT_BUS")
    assert result["name"] == "AGENT_BUS"
    assert result["created"] == "2026-08-29T00:14:02Z"
    # The *full* blocks, not a projection — storage and num_subjects are only
    # reachable this way.
    assert result["config"]["storage"] == "file"
    assert result["config"]["subjects"] == ["events.agent-bus.>"]
    assert result["state"]["num_subjects"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_get_stream_unknown_raises():
    """A missing stream must raise, not return {} — those are different facts."""
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json=_JSZ_STREAMS))
    from nats_mcp.server import get_stream

    with pytest.raises(NatsMcpError, match="not found"):
        await get_stream("NOPE")


@pytest.mark.asyncio
@respx.mock
async def test_get_stream_is_case_sensitive():
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json=_JSZ_STREAMS))
    from nats_mcp.server import get_stream

    with pytest.raises(NatsMcpError):
        await get_stream("agent_bus")


@pytest.mark.asyncio
@respx.mock
async def test_get_consumers_reports_lag():
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json=_JSZ_STREAMS))
    from nats_mcp.server import get_consumers

    result = await get_consumers("AGENT_BUS")
    assert result["stream"] == "AGENT_BUS"
    assert result["num_consumers"] == 1
    consumer = result["consumers"][0]
    assert consumer["name"] == "bus-reader"
    assert consumer["num_pending"] == 12
    assert consumer["num_ack_pending"] == 3
    assert consumer["num_redelivered"] == 1
    # Flattened out of the nested delivered/ack_floor blocks.
    assert consumer["delivered_stream_seq"] == 56
    assert consumer["ack_floor_stream_seq"] == 53


@pytest.mark.asyncio
@respx.mock
async def test_get_consumers_empty_is_not_an_error():
    """AGENT_BUS genuinely has zero consumers on forge — [] is the correct answer."""
    body = {
        "account_details": [
            {"name": "$G", "stream_detail": [{"name": "AGENT_BUS", "state": {"consumer_count": 0}}]}
        ]
    }
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json=body))
    from nats_mcp.server import get_consumers

    result = await get_consumers("AGENT_BUS")
    assert result["num_consumers"] == 0
    assert result["consumers"] == []


@pytest.mark.asyncio
@respx.mock
async def test_get_consumers_unknown_stream_raises():
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json=_JSZ_STREAMS))
    from nats_mcp.server import get_consumers

    with pytest.raises(NatsMcpError, match="not found"):
        await get_consumers("NOPE")


@pytest.mark.asyncio
@respx.mock
async def test_streams_span_multiple_accounts():
    """stream_detail is nested per account — a single-account walk would miss these."""
    body = {
        "account_details": [
            {"name": "$G", "stream_detail": [{"name": "A", "config": {}, "state": {}}]},
            {"name": "OTHER", "stream_detail": [{"name": "B", "config": {}, "state": {}}]},
        ]
    }
    respx.get("http://localhost:8222/jsz").mock(return_value=Response(200, json=body))
    from nats_mcp.server import get_stream, get_streams

    assert {s["name"] for s in (await get_streams())["streams"]} == {"A", "B"}
    assert (await get_stream("B"))["name"] == "B"


# ── get_health parameters ────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, {}),
        ({"js_enabled_only": True}, {"js-enabled-only": "true"}),
        ({"js_server_only": True}, {"js-server-only": "true"}),
        (
            {"js_enabled_only": True, "js_server_only": True},
            {"js-enabled-only": "true", "js-server-only": "true"},
        ),
    ],
)
async def test_get_health_passes_js_params(kwargs, expected):
    """NATS spells these js-enabled-only / js-server-only, not js_enabled_only."""
    captured = []

    def capture(request):
        captured.append(dict(request.url.params))
        return Response(200, json={"status": "ok"})

    respx.get("http://localhost:8222/healthz").mock(side_effect=capture)
    from nats_mcp.server import get_health

    assert (await get_health(**kwargs))["status"] == "ok"
    assert captured[0] == expected


# ── HTTP status errors ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_non_2xx_raises_nats_monitoring_error(status):
    """A non-2xx must not reach the MCP caller as a raw httpx exception.

    raise_for_status() was already being called and nothing caught it, so an
    httpx.HTTPStatusError propagated out with the full request URL in its repr.
    """
    respx.get("http://localhost:8222/varz").mock(return_value=Response(status, json={}))
    from nats_mcp.server import get_server_stats

    with pytest.raises(NatsMonitoringError) as exc_info:
        await get_server_stats()
    assert exc_info.value.status_code == status
    assert exc_info.value.path == "/varz"
    # Every failure mode is catchable as the one base class.
    assert isinstance(exc_info.value, NatsMcpError)


def test_all_nats_errors_share_a_base():
    for exc in (NatsUnreachableError, NatsTimeoutError, NatsMonitoringError):
        assert issubclass(exc, NatsMcpError)


# ── Tool registration ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_tools_registered_with_real_schemas():
    """The @instrument wrapper must not flatten the tools' parameter schemas.

    A bare *args/**kwargs wrapper would register every tool with an empty schema
    and no MCP client could pass an argument — a failure invisible to any test
    that calls the tool functions directly.
    """
    from nats_mcp.server import mcp

    tools = {t.name: t for t in await mcp._list_tools()}
    assert set(tools) == {
        "get_server_stats",
        "get_connections",
        "get_subscription_stats",
        "get_jetstream_status",
        "get_streams",
        "get_stream",
        "get_consumers",
        "get_health",
    }
    assert set(tools["get_connections"].parameters["properties"]) == {"limit", "state"}
    assert set(tools["get_health"].parameters["properties"]) == {
        "js_enabled_only",
        "js_server_only",
    }
    assert set(tools["get_stream"].parameters["properties"]) == {"name"}
    assert set(tools["get_consumers"].parameters["properties"]) == {"stream"}


# ── main() transport selection ───────────────────────────────────────────────


def test_fmt_uptime_handles_missing_value():
    from nats_mcp.server import _fmt_uptime

    assert _fmt_uptime(None) == "unknown"


def test_main_defaults_to_stdio(monkeypatch):
    from nats_mcp import server

    monkeypatch.delenv("NATS_MCP_PORT", raising=False)
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(server, "atexit", type("A", (), {"register": staticmethod(lambda f: None)}))
    server.main()
    assert calls == [((), {})]


def _stub_run(monkeypatch, server):
    """Capture mcp.run kwargs and neutralise atexit."""
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(server, "atexit", type("A", (), {"register": staticmethod(lambda f: None)}))
    return calls


_GOOD_TOKEN = "x" * 32


def test_main_http_binds_loopback_by_default(monkeypatch):
    from nats_mcp import server

    monkeypatch.setenv("NATS_MCP_PORT", "8508")
    monkeypatch.delenv("NATS_MCP_HOST", raising=False)
    monkeypatch.delenv("NATS_MCP_ALLOW_NONLOOPBACK", raising=False)
    monkeypatch.setenv("NATS_MCP_API_TOKEN", _GOOD_TOKEN)
    calls = _stub_run(monkeypatch, server)
    server.main()
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8508
    assert calls[0]["transport"] == "streamable-http"
    # Auth is no longer conditional — HTTP mode always carries the middleware,
    # because it cannot start without a token at all.
    assert len(calls[0]["middleware"]) == 1


def test_main_http_refuses_without_a_token(monkeypatch):
    """The MEDIUM-1 case: no token must refuse, not warn and serve.

    A log line is not an access control — nothing observes it unless someone is
    already tailing startup output, and the process serves every tool meanwhile.
    """
    from nats_mcp import server

    monkeypatch.setenv("NATS_MCP_PORT", "8508")
    monkeypatch.delenv("NATS_MCP_API_TOKEN", raising=False)
    calls = _stub_run(monkeypatch, server)
    with pytest.raises(RuntimeError, match="without NATS_MCP_API_TOKEN"):
        server.main()
    assert not calls, "server started despite refusing"


def test_main_http_refuses_a_short_token(monkeypatch):
    from nats_mcp import server

    monkeypatch.setenv("NATS_MCP_PORT", "8508")
    monkeypatch.setenv("NATS_MCP_API_TOKEN", "s3cret")
    calls = _stub_run(monkeypatch, server)
    with pytest.raises(RuntimeError, match="too short"):
        server.main()
    assert not calls


def test_main_http_refuses_nonloopback_without_optin(monkeypatch):
    """0.0.0.0 must be an explicit, auditable opt-in — not a side effect of one env var."""
    from nats_mcp import server

    monkeypatch.setenv("NATS_MCP_PORT", "8508")
    monkeypatch.setenv("NATS_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("NATS_MCP_API_TOKEN", _GOOD_TOKEN)
    monkeypatch.delenv("NATS_MCP_ALLOW_NONLOOPBACK", raising=False)
    calls = _stub_run(monkeypatch, server)
    with pytest.raises(RuntimeError, match="non-loopback"):
        server.main()
    assert not calls


@pytest.mark.parametrize("optin", ["1", "true", "TRUE", "yes"])
def test_main_http_allows_nonloopback_with_optin(optin, monkeypatch):
    """Build 2's configuration: 0.0.0.0 + explicit opt-in + a real token."""
    from nats_mcp import server

    monkeypatch.setenv("NATS_MCP_PORT", "8508")
    monkeypatch.setenv("NATS_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("NATS_MCP_ALLOW_NONLOOPBACK", optin)
    monkeypatch.setenv("NATS_MCP_API_TOKEN", _GOOD_TOKEN)
    calls = _stub_run(monkeypatch, server)
    server.main()
    assert calls[0]["host"] == "0.0.0.0"
    assert len(calls[0]["middleware"]) == 1


@pytest.mark.parametrize("optin", ["0", "false", "no", "", "maybe"])
def test_nonloopback_optin_rejects_non_truthy_values(optin, monkeypatch):
    """A typo'd opt-in must fail closed, not fall through to permitted."""
    from nats_mcp import server

    monkeypatch.setenv("NATS_MCP_PORT", "8508")
    monkeypatch.setenv("NATS_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("NATS_MCP_ALLOW_NONLOOPBACK", optin)
    monkeypatch.setenv("NATS_MCP_API_TOKEN", _GOOD_TOKEN)
    _stub_run(monkeypatch, server)
    with pytest.raises(RuntimeError, match="non-loopback"):
        server.main()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_need_no_optin(host, monkeypatch):
    from nats_mcp import server

    monkeypatch.setenv("NATS_MCP_PORT", "8508")
    monkeypatch.setenv("NATS_MCP_HOST", host)
    monkeypatch.delenv("NATS_MCP_ALLOW_NONLOOPBACK", raising=False)
    monkeypatch.setenv("NATS_MCP_API_TOKEN", _GOOD_TOKEN)
    calls = _stub_run(monkeypatch, server)
    server.main()
    assert calls[0]["host"] == host


def test_stdio_mode_needs_no_token(monkeypatch):
    """The guards are HTTP-only. stdio has no network surface and must stay unchanged."""
    from nats_mcp import server

    monkeypatch.delenv("NATS_MCP_PORT", raising=False)
    monkeypatch.delenv("NATS_MCP_API_TOKEN", raising=False)
    monkeypatch.setenv("NATS_MCP_HOST", "0.0.0.0")  # ignored without a port
    calls = _stub_run(monkeypatch, server)
    server.main()
    assert calls == [{}]


def test_main_logs_when_otel_enabled(monkeypatch):
    """OTEL init runs at startup, not lazily on the first tool call.

    get_tracer() was defined and called from nowhere before v0.2.0 — setting
    OTEL_EXPORTER_OTLP_ENDPOINT did nothing while the README documented it as
    supported. This asserts main() actually reaches it.
    """
    from nats_mcp import server

    monkeypatch.delenv("NATS_MCP_PORT", raising=False)
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)
    monkeypatch.setattr(server, "atexit", type("A", (), {"register": staticmethod(lambda f: None)}))
    monkeypatch.setattr("nats_mcp.observability.get_tracer", lambda: object(), raising=True)
    events = []
    monkeypatch.setattr(server._log, "info", lambda e, **k: events.append(e), raising=False)
    server.main()
    assert "nats_mcp_otel_enabled" in events


def test_fmt_uptime_passes_through_a_string():
    """/varz emits uptime as a formatted string, not a nanosecond integer.

    get_server_stats raised TypeError against every real NATS server before
    v0.2.0 — `"7h33m0s" // 1_000_000_000`. The old fixture supplied an integer,
    which NATS does not emit, so nothing caught it.
    """
    from nats_mcp.server import _fmt_uptime

    assert _fmt_uptime("7h33m0s") == "7h33m0s"


@pytest.mark.asyncio
@respx.mock
async def test_get_server_stats_with_real_varz_shape():
    """Field types exactly as NATS 2.12.6 emits them, captured live 2026-08-30."""
    respx.get("http://localhost:8222/varz").mock(
        return_value=Response(
            200,
            json={
                "version": "2.12.6",
                "uptime": "7h33m0s",  # a string, not an integer
                "start": "2026-08-30T06:04:03.76773166Z",
                "connections": 1,
                "total_connections": 2,
                "subscriptions": 66,
                "slow_consumers": 0,
                "mem": 13357056,
                "cpu": 0,
                "in_msgs": 0,
                "out_msgs": 0,
                "in_bytes": 0,
                "out_bytes": 0,
                "server_id": "NBMU6GRMDDDYQB74O6EWIQO77BSPBK2GFRDEXAPO6ICBMCZP2SDA6E33",
            },
        )
    )
    from nats_mcp.server import get_server_stats

    result = await get_server_stats()
    assert result["uptime"] == "7h33m0s"
    assert result["version"] == "2.12.6"
    assert result["subscriptions"] == 66
    assert "server_id" not in result
