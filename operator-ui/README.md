# operator-ui (Phase 3)

Operator console (Next.js 15, TypeScript, Tailwind): active agent sessions, live
token and cost burn, circuit-breaker alerts, and the approval queue (one-click
approve/reject with JSON payload inspection).

Contract:

- Talks only to the gateway's control-plane API, authenticated as a human operator.
  What each user can see or do comes from `actions` in `config/policies.yaml`
  (`approvals:read`, `approvals:decide`, `sessions:read`, `circuit_breakers:reset`,
  `policy:read`).
- Live updates arrive over the gateway's server-sent event stream.
