# operator-ui

The operator console: the approval queue with one-click approve or reject and the exact
JSON payload, live per-session token and cost burn, circuit-breaker and budget alerts,
and a live event feed. It is built with Next.js 15 (App Router), TypeScript and
Tailwind CSS v4.

It is a backend-for-frontend:
- the browser talks only to this app;
- route handlers under `src/app/api` call the gateway with the operator's token;
- that token lives in an encrypted, httpOnly, SameSite=Strict cookie.

```bash
pnpm install --frozen-lockfile
pnpm check      # ESLint, TypeScript, Vitest
pnpm build      # standalone server in .next/standalone
make ui-dev     # from the repository root: hot reload against the local gateway
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GATEWAY_URL` | `http://localhost:8000` | the gateway's control-plane API (server-side only) |
| `SESSION_SECRET` | required, at least 32 characters | encrypts the session cookie |
| `DEV_SIGNIN` | `false` | local stack only: sign in by minting tokens with `GATEWAY_JWT_SECRET` |
| `GATEWAY_JWT_SECRET`, `GATEWAY_JWT_ISSUER`, `GATEWAY_JWT_AUDIENCE` | -, `http://localhost/dev-idp`, `mcp-gateway` | development token minting |
| `CONSOLE_TENANT` | `acme` | tenant of development sign-ins |
| `COOKIE_SECURE` | `true` in production | set `false` only for plain-HTTP localhost |

With `DEV_SIGNIN=false` the console accepts tokens issued by the identity provider. They
must carry an `operator` or `auditor` role, and they are checked against the gateway
before a session starts.
