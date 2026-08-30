# syntax=docker/dockerfile:1

# python:3.13-slim, pinned by multi-arch index digest rather than by tag — the tag
# moves. Re-resolve with `docker buildx imagetools inspect python:3.13-slim` when
# bumping; do not copy the digest from a sibling repo, which will be pinned to a
# different snapshot.
#
# slim rather than alpine: under the [otel] extra the tree includes grpcio, which
# publishes manylinux wheels but whose musl coverage is the kind of thing that
# quietly turns into a build toolchain living in the runtime image.
ARG PYTHON_IMAGE=python:3.13-slim@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f

FROM ${PYTHON_IMAGE} AS build

# Build into a self-contained venv so the runtime stage can take the tree wholesale
# without pip, setuptools, or any build metadata coming with it.
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /src
# pyproject, README and LICENSE are all read by the setuptools build; copying just
# these plus the package keeps the layer cache from busting on a docs or test edit.
COPY pyproject.toml README.md LICENSE ./
COPY nats_mcp ./nats_mcp
# [otel] is installed deliberately, not incidentally. Setting
# OTEL_EXPORTER_OTLP_ENDPOINT without the extra present does not fail loudly — the
# exporter import fails once and then nothing is ever exported, which is a failure
# mode this fleet has already shipped into (vikunja#336). v0.2.0 wires traces and
# metrics and initialises them eagerly in main(), so the extra is required here,
# not merely prudent.
RUN pip install --no-cache-dir '.[otel]'

FROM ${PYTHON_IMAGE} AS runtime

# NATS_MCP_HOST=0.0.0.0 is required for the server to be reachable from outside its
# own network namespace, and is NOT an exposure decision — a bind address is a no-op
# as a security control inside a namespace. The compose `ports:` publish is the
# actual control, and the fragment in deploy/ publishes loopback-only. Do not
# "harden" this back to 127.0.0.1: that produces a container that reports healthy
# while every tool fails, which is exactly what vikunja#462 was.
#
# NATS_MCP_ALLOW_NONLOOPBACK=1 is NOT redundant with the line above. As of v0.2.0
# main() fails closed and *refuses to start* on a non-loopback bind without this
# opt-in (audit 2026-08-30 MEDIUM-1). Widening the bind is therefore a deliberate,
# auditable choice rather than a side effect of one variable. It is baked here
# because running in a namespace is inherent to this image, not a deployment choice.
#
# Deliberately absent:
#   NATS_MONITOR_URL   — a baked default of localhost:8222 is the container's OWN
#                        loopback, where nothing is listening. It must come from
#                        compose as http://nats:8222 (the service name on forge-net).
#   NATS_MCP_API_TOKEN — never bake a credential into a layer. main() refuses to
#                        start without one of at least 16 characters, so an image
#                        run with no token fails loudly instead of serving openly.
#   LOG_FILE           — must stay unset. Logs go to stdout, which is what makes
#                        read_only: true viable with no writable path at all.
ENV NATS_MCP_HOST=0.0.0.0 \
    NATS_MCP_ALLOW_NONLOOPBACK=1 \
    NATS_MCP_PORT=8508 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=build /opt/venv /opt/venv

# Non-root, fixed uid/gid 1000 rather than a name lookup — matches the
# `user: "1000:1000"` the nats service in the same stack already runs as.
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --no-create-home app
USER 1000:1000

EXPOSE 8508

# /health is unauthenticated by design (exempted by exact path in
# _BearerAuthMiddleware) and returns a bare status, so this needs no token — which
# matters, because a HEALTHCHECK carrying the bearer token would put a credential in
# every `docker inspect`.
#
# It is a pure liveness check: it does not probe NATS, so a NATS restart does not
# mark this container unhealthy and trigger a pointless restart of a working process.
#
# python rather than wget/curl: slim ships neither, and adding one to the runtime
# image for a healthcheck is a worse trade than using the interpreter already here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('NATS_MCP_PORT','8508')+'/health', timeout=4).status==200 else 1)" \
  || exit 1

CMD ["nats-mcp"]
