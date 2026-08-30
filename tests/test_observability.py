"""Tests for nats_mcp/observability.py — logging sinks and OTEL instrumentation.

This module used to be excluded from the coverage gate, so none of it was ever
measured. The logging behaviour here is not cosmetic: build 2 puts this process
in a container, where a file sink is invisible to `docker logs` and grows
unbounded on a bind mount.
"""

from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from nats_mcp import observability
from nats_mcp.observability import configure_logging, instrument, observe_tool


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch):
    """Restore logger state and the OTEL globals between tests."""
    app_logger = logging.getLogger("nats-mcp")
    saved_handlers = list(app_logger.handlers)
    saved_level = app_logger.level
    saved_propagate = app_logger.propagate
    for var in ("LOG_FILE", "LOG_LEVEL", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)
    yield
    app_logger.handlers[:] = saved_handlers
    app_logger.setLevel(saved_level)
    app_logger.propagate = saved_propagate
    observability._tracer = None
    observability._tracer_provider = None
    observability._meter_provider = None
    observability._call_counter = None
    observability._duration_histogram = None
    observability._otel_failed = False


def _handlers():
    return logging.getLogger("nats-mcp").handlers


# ── Log sinks ────────────────────────────────────────────────────────────────


def test_no_file_handler_when_log_file_unset():
    """The default must be stdout only.

    The old default was a hardcoded /opt/appdata/nats-mcp/logs path, so every run
    created a directory and an unrotated file whether or not anyone wanted one.
    """
    configure_logging()
    assert not any(isinstance(h, logging.FileHandler) for h in _handlers())


def test_logs_go_to_stdout_not_stderr():
    """`docker logs` and the json-file driver read stdout."""
    configure_logging()
    streams = [h.stream for h in _handlers() if isinstance(h, logging.StreamHandler)]
    assert sys.stdout in streams
    assert sys.stderr not in streams


def test_log_file_env_attaches_file_handler(tmp_path, monkeypatch):
    target = tmp_path / "logs" / "nats-mcp.log"
    monkeypatch.setenv("LOG_FILE", str(target))
    configure_logging()
    assert any(isinstance(h, logging.FileHandler) for h in _handlers())
    assert target.parent.is_dir()


def test_log_file_bare_filename_does_not_raise(tmp_path, monkeypatch):
    """Covers the L1 fix (commit 81de0ac), which had no test.

    A bare filename has no directory part, and os.makedirs("") raises
    FileNotFoundError — which would crash startup.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_FILE", "nats-mcp.log")
    configure_logging()  # must not raise
    assert any(isinstance(h, logging.FileHandler) for h in _handlers())


def test_unwritable_log_file_falls_back_to_stdout(monkeypatch, capsys):
    """A read-only fs must not take the process down — stdout is the primary sink."""
    monkeypatch.setenv("LOG_FILE", "/proc/nats-mcp-cannot-write-here/x.log")
    configure_logging()
    assert not any(isinstance(h, logging.FileHandler) for h in _handlers())
    assert any(isinstance(h, logging.StreamHandler) for h in _handlers())
    assert "file logging disabled" in capsys.readouterr().err


def test_root_logger_is_left_alone():
    """Attaching to root captured every third-party record at the app's level.

    Same untyped-noise pattern remediated in dockhand-mcp (vikunja#574) and
    task-dispatcher (#552).
    """
    root = logging.getLogger()
    before = list(root.handlers)
    configure_logging()
    assert root.handlers == before


@pytest.mark.parametrize("name", ["httpx", "httpcore", "mcp", "fastmcp", "uvicorn", "starlette"])
def test_third_party_loggers_demoted_to_warning(name):
    configure_logging()
    assert logging.getLogger(name).level == logging.WARNING


def test_app_logger_honours_log_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging()
    assert logging.getLogger("nats-mcp").level == logging.DEBUG


def test_invalid_log_level_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    configure_logging()
    assert logging.getLogger("nats-mcp").level == logging.INFO


def test_app_logger_does_not_propagate():
    configure_logging()
    assert logging.getLogger("nats-mcp").propagate is False


def test_output_is_json():
    configure_logging()
    buffer = io.StringIO()
    app_logger = logging.getLogger("nats-mcp")
    for h in app_logger.handlers:
        h.stream = buffer
    import structlog

    structlog.get_logger("nats-mcp").info("probe_event", detail="value")
    payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert payload["event"] == "probe_event"
    assert payload["detail"] == "value"
    assert payload["level"] == "info"


# ── OTEL gating ──────────────────────────────────────────────────────────────


def test_get_tracer_returns_none_when_unconfigured():
    """No endpoint is the intended disabled path — silent, and not a failure."""
    assert observability.get_tracer() is None
    assert observability._otel_failed is False


def test_otel_init_failure_warns_once_and_does_not_retry(monkeypatch):
    """A configured-but-broken backend must warn once, not on every tool call.

    Re-entering the init path per call is what turned one bad env var into 2,867
    error lines in dockhand-mcp (vikunja#574).
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    attempts = []
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def exploding_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            attempts.append(name)
            raise ImportError("simulated missing [otel] extra")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", exploding_import)
    warnings = []
    monkeypatch.setattr(
        observability.log, "warning", lambda *a, **k: warnings.append((a, k)), raising=False
    )

    assert observability.get_tracer() is None
    assert observability._otel_failed is True
    first_attempts = len(attempts)
    assert warnings and warnings[0][0][0] == "otel_init_failed"

    observability.get_tracer()
    observability.get_tracer()
    assert len(attempts) == first_attempts, "init retried after failing"
    assert len(warnings) == 1, "warned more than once"


def test_shutdown_is_safe_when_otel_never_started():
    observability.shutdown_observability()  # must not raise


# ── observe_tool / instrument ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_instrument_preserves_signature_and_result():
    @instrument("sample")
    async def sample(a: int, b: str = "x") -> dict:
        """Docstring kept."""
        return {"a": a, "b": b}

    import inspect
    import typing

    assert await sample(1, b="y") == {"a": 1, "b": "y"}
    assert sample.__name__ == "sample"
    assert sample.__doc__ == "Docstring kept."
    # FastMCP builds each tool's JSON schema from the signature and the resolved
    # type hints. get_type_hints, not __annotations__ — this module has
    # `from __future__ import annotations`, so the raw values are strings.
    assert list(inspect.signature(sample).parameters) == ["a", "b"]
    hints = typing.get_type_hints(sample)
    assert hints["a"] is int
    assert hints["b"] is str


@pytest.mark.asyncio
async def test_instrument_propagates_exceptions():
    @instrument("boom")
    async def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        await boom()


def test_observe_tool_is_a_noop_without_otel():
    with observe_tool("anything", endpoint="/varz"):
        pass  # no exporter configured — must not raise


def test_observe_tool_records_metrics_when_configured():
    """Counter and histogram fire on the success path, labelled by tool and outcome."""
    counted, timed = [], []
    observability._call_counter = type(
        "C", (), {"add": lambda self, v, labels: counted.append((v, labels))}
    )()
    observability._duration_histogram = type(
        "H", (), {"record": lambda self, v, labels: timed.append((v, labels))}
    )()
    observability._otel_failed = True  # skip real init

    with observe_tool("get_streams"):
        pass

    assert counted == [(1, {"tool": "get_streams", "outcome": "ok"})]
    assert timed[0][1] == {"tool": "get_streams", "outcome": "ok"}
    assert timed[0][0] >= 0


def test_observe_tool_labels_failures_by_exception_class():
    counted = []
    observability._call_counter = type(
        "C", (), {"add": lambda self, v, labels: counted.append((v, labels))}
    )()
    observability._otel_failed = True

    with pytest.raises(KeyError), observe_tool("get_stream"):
        raise KeyError("missing")

    assert counted == [(1, {"tool": "get_stream", "outcome": "KeyError"})]


# ── OTEL success path ────────────────────────────────────────────────────────
#
# These need the [otel] extra. CI installs ".[dev,otel]" so they run there; the
# skip guard only covers a developer who installed ".[dev]" alone. Without these
# the success path is unexecuted, which is exactly the state v0.2.0 is fixing —
# get_tracer() existed and was never called from anywhere.

otel_sdk = pytest.importorskip("opentelemetry.sdk.trace", reason="[otel] extra not installed")


@pytest.fixture
def otel_endpoint(monkeypatch):
    # Nothing is exported during a unit test — BatchSpanProcessor and
    # PeriodicExportingMetricReader both buffer, and the test never flushes. The
    # endpoint only has to be syntactically valid for the exporters to build.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


def test_otel_init_builds_tracer_and_instruments(otel_endpoint):
    tracer = observability.get_tracer()
    assert tracer is not None
    assert observability._call_counter is not None
    assert observability._duration_histogram is not None
    assert observability._otel_failed is False


def test_otel_init_is_idempotent(otel_endpoint):
    first = observability.get_tracer()
    assert observability.get_tracer() is first


def test_observe_tool_sets_span_attributes(otel_endpoint):
    """Attributes reach the span, so the endpoint path is queryable in SigNoz."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observability._tracer = provider.get_tracer("nats-mcp")
    observability._otel_failed = True  # skip re-init

    with observe_tool("get_connections", endpoint="/connz"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "nats_mcp.get_connections"
    assert spans[0].attributes["endpoint"] == "/connz"


def test_shutdown_flushes_and_clears_providers(otel_endpoint):
    observability.get_tracer()
    assert observability._tracer_provider is not None
    observability.shutdown_observability()
    assert observability._tracer_provider is None
    assert observability._meter_provider is None


def test_shutdown_survives_a_failing_provider(monkeypatch):
    """A flush that raises must not take the process down on exit."""

    class Exploding:
        def shutdown(self):
            raise RuntimeError("collector gone")

    observability._tracer_provider = Exploding()
    observability._meter_provider = None
    warnings = []
    monkeypatch.setattr(
        observability.log, "warning", lambda *a, **k: warnings.append(a), raising=False
    )
    observability.shutdown_observability()  # must not raise
    assert warnings and warnings[0][0] == "otel_shutdown_failed"
    assert observability._tracer_provider is None
