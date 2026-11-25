# syntax=docker/dockerfile:1.7
# Operator console (Next.js 15, standalone output). Build context: operator-ui/.
#   docker build -t operator-ui operator-ui

ARG NODE_IMAGE=node:22.23.5-alpine

# --- dependencies: exactly what pnpm-lock.yaml pins --------------------------------------
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# --- build ----------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS build
WORKDIR /app
RUN corepack enable
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

# --- runtime: the standalone server only, non-root, read-only friendly ----------------------
FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:3000/api/health || exit 1
CMD ["node", "server.js"]
