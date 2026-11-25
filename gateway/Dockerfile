# syntax=docker/dockerfile:1.7
# MCP Enterprise Gateway image. Build context: the repository root (uv workspace).
#   docker build -f gateway/Dockerfile -t mcp-gateway .

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie

# --- build: resolve exactly what uv.lock pins ------------------------------------------
FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src

# Third-party dependencies first: this layer is reused until uv.lock changes.
COPY pyproject.toml uv.lock ./
COPY gateway/pyproject.toml gateway/
COPY mcp-servers/crm-ops/pyproject.toml mcp-servers/crm-ops/
COPY libs/platform-telemetry/pyproject.toml libs/platform-telemetry/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package mcp-gateway --no-install-workspace

COPY gateway gateway
COPY libs/platform-telemetry libs/platform-telemetry
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package mcp-gateway --no-editable

# --- runtime: no build tools, no sources, non-root, read-only friendly -----------------
FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 gateway \
 && useradd --system --uid 10001 --gid gateway --no-create-home --shell /usr/sbin/nologin gateway
COPY --from=build /app/.venv /app/.venv
# The policy is part of the image: every policy change ships through review and a deploy,
# and the running policy is identified by its digest in every decision.
COPY config/policies.yaml /app/config/policies.yaml
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GATEWAY_POLICY_PATH=/app/config/policies.yaml
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status != 200)"]
ENTRYPOINT ["mcp-gateway"]
