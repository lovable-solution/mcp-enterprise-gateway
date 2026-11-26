# operator-ui

The operator console, in five views:
- **Overview:** live traffic through the gateway, key figures with sparklines, held
  calls, alerts and the busiest sessions.
- **Approvals:** held and decided calls, each with the requested change, the agent's
  reasoning, the exact payload and the audit trail.
- **Sessions:** spend, tokens and tool calls against each session's caps, and breaker
  state and reset.
- **Activity:** every gateway event of the last 15 minutes, then live, with filters.
- **Policy:** the enforced role × tool matrix, upstream health and each role's console
  actions.

It is built with Next.js 15 (App Router), TypeScript, Tailwind CSS v4 and Motion. The
fonts are Mona Sans and Martian Mono (SIL OFL), bundled with the app, so no request
leaves for a font CDN. Every animation follows the OS *reduce motion* setting.

It is a backend-for-frontend:
- the browser talks only to this app;
- route handlers under `src/app/api` call the gateway with the operator's token;
- that token lives in an encrypted, httpOnly, SameSite=Strict cookie.

```bash
pnpm install --frozen-lockfile
pnpm check          # ESLint, TypeScript, Vitest
pnpm build          # standalone server in .next/standalone
make ui-dev         # from the repository root: hot reload against the local gateway
make demo-traffic   # from the repository root: live demo traffic, no model calls
```

## Layout

| Path | Contents |
| --- | --- |
| `src/app/(console)` | the five views; their shared layout checks the session |
| `src/app/api` | the backend-for-frontend routes to the gateway |
| `src/components/console` | `ConsoleProvider`: the snapshot, the event stream, actions and toasts |
| `src/components/ui` | the design system: tokens in `globals.css`, chips, meters, the hold button |
| `src/lib` | pure, unit-tested logic: the dashboard reducer, traffic, presentation, validation |

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
