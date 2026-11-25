# syntax=docker/dockerfile:1.7
# crm-ops MCP server image. Build context: the repository root (uv workspace).
#   docker build -f mcp-servers/crm-ops/Dockerfile -t crm-ops-mcp .

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src

COPY pyproject.toml uv.lock ./
COPY gateway/pyproject.toml gateway/
COPY mcp-servers/crm-ops/pyproject.toml mcp-servers/crm-ops/
COPY libs/platform-telemetry/pyproject.toml libs/platform-telemetry/
COPY agent-runtime/pyproject.toml agent-runtime/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package crm-ops-mcp --no-install-workspace

COPY mcp-servers/crm-ops mcp-servers/crm-ops
COPY libs/platform-telemetry libs/platform-telemetry
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package crm-ops-mcp --no-editable

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 crmops \
 && useradd --system --uid 10001 --gid crmops --no-create-home --shell /usr/sbin/nologin crmops
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2).status != 200)"]
ENTRYPOINT ["crm-ops-mcp"]
