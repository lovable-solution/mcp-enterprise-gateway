# syntax=docker/dockerfile:1.7
# Governed agent runtime image (LangGraph + PydanticAI). Build context: the repository root.
#   docker build -f agent-runtime/Dockerfile -t agent-runtime .
#   docker compose run --rm agent-runtime run "Check Cedar Analytics' receivables" --wait

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
COPY agent-runtime/pyproject.toml agent-runtime/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package agent-runtime --no-install-workspace

COPY agent-runtime agent-runtime
COPY libs/platform-telemetry libs/platform-telemetry
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package agent-runtime --no-editable

# --- runtime: no build tools, no sources, non-root, read-only friendly -----------------
FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10002 agent \
 && useradd --system --uid 10002 --gid agent --no-create-home --shell /usr/sbin/nologin agent
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYDANTIC_AI_NO_BANNER=1
WORKDIR /app
USER 10002:10002
# A job, not a server: each invocation runs (or resumes) one thread and exits. Threads
# live in PostgreSQL, so any later invocation, on any host, can resume them.
ENTRYPOINT ["agent-runtime"]
CMD ["--help"]
