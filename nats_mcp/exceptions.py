"""Exception hierarchy for nats-mcp.

Every failure path out of the NATS monitoring API raises one of these rather
than a builtin (``ConnectionError``/``TimeoutError``) or a raw ``httpx``
exception. An MCP caller can then catch ``NatsMcpError`` alone and know it has
covered the whole surface.
"""

from __future__ import annotations


class NatsMcpError(Exception):
    """Base class for every nats-mcp failure."""


class NatsUnreachableError(NatsMcpError):
    """The NATS monitoring API could not be contacted at all."""


class NatsTimeoutError(NatsMcpError):
    """The NATS monitoring API accepted the connection but did not respond in time."""


class NatsMonitoringError(NatsMcpError):
    """The NATS monitoring API returned a non-2xx response.

    Carries the status code and the endpoint path so the caller can tell a 404
    (wrong path / feature off) from a 503 (server starting or unhealthy) without
    parsing a message string.
    """

    def __init__(self, status_code: int, path: str, message: str | None = None) -> None:
        self.status_code = status_code
        self.path = path
        super().__init__(message or f"NATS monitoring API returned {status_code} for {path}")
