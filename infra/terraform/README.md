# Terraform (AWS)

Deploys the gateway, the MCP servers and the operator console as private ECS Fargate
services behind an internal ALB, with RDS PostgreSQL, ElastiCache and Secrets Manager.
The agent runtime and the database bootstrap are on-demand tasks.

## Layout

```
environments/dev/      root module: composition only (no resources of its own)
  db-bootstrap.sh      idempotent role/database setup run by the db-bootstrap task
  tests/               the whole environment, planned and applied against mocked AWS
modules/
  vpc/                 3 tiers per AZ (public | private-app | private-data), NAT,
                       VPC endpoints, flow logs, default security group locked down
  secrets_manager/     KMS key (rotating) + secrets; generated values are write-only
  rds_postgres/        TLS-only Postgres; RDS-managed, rotated master credential
  elasticache_redis/   TLS + AUTH (write-only), noeviction, Multi-AZ failover
  ecs_fargate/         cluster, internal ALB, Service Connect, per-workload security
                       groups, roles and logs, services, on-demand tasks, autoscaling
  */tests/             plan/apply tests with mocked providers (make tf-test)
tests/mocks/aws/       shared mock values for those tests
```

## Trust topology

| From | To | How | Allowed by |
| --- | --- | --- | --- |
| `alb_ingress_cidrs` | ALB :443 | HTTPS (TLS 1.3 / 1.2 with forward secrecy) | ALB security group |
| ALB | gateway :8000 | `/mcp`, `/mcp/*`, `/v1/*`, `/healthz`, `/readyz` (priority 100) | gateway SG: from the ALB SG |
| ALB | operator-ui :3000 | everything else (priority 1000) | console SG: from the ALB SG |
| operator-ui | gateway :8000 | Service Connect (`gateway.mcp.internal`) | gateway SG: from the console SG |
| gateway | crm-ops :8001 | Service Connect (`crm-ops.mcp.internal`) + signed identity assertion | crm-ops SG: from the gateway SG **only** |
| gateway | RDS :5432, ElastiCache :6379 | TLS | data-store SGs: from the gateway SG |
| agent-runtime task | ALB :443, RDS :5432 | like any agent; its own checkpoint database | ALB and RDS SGs: from the task SG |
| db-bootstrap task | RDS :5432 | the only reader of the master secret | RDS SG: from the task SG |

- **No public addresses.** Nothing gets a public IP. The ALB is internal, and only the
  gateway and the console have listener rules.
- **No route out from the data tier.** Its subnets have local routes only.
- **Least-privilege egress.** Every workload has its own security group, which allows
  HTTPS out (AWS APIs through the VPC endpoints, the identity provider's JWKS, the
  Claude API) plus exactly the services and data stores it calls.

## Secrets: nothing in code, tfvars, plans or state

| Secret | Created by | Consumed via |
| --- | --- | --- |
| RDS master credential | RDS (`manage_master_user_password`, auto-rotated) | the db-bootstrap task only (ECS `secrets`, JSON keys) |
| Gateway / agent DB passwords, Redis AUTH token, upstream assertion key, webhook HMAC key, console session secret | ephemeral `random_password` -> `secret_string_wo` (write-only) | ECS `secrets` (valueFrom); ElastiCache reads its token back ephemerally into `auth_token_wo` |
| Claude API key, agent gateway token | you, after the first apply (`put-secret-value`) | the agent-runtime task only |
| JWT verification | none: the identity provider's public JWKS URL | gateway setting |

Each workload's execution role can read only the secrets that workload declares (tests
assert this).

## Deploy

Prerequisites:
- an S3 state bucket (versioned, SSE-KMS);
- an ACM certificate for `gateway_hostname`;
- images built by CI, pushed to ECR and referenced by digest;
- optionally, a Route 53 private hosted zone for the hostname.

```bash
cd environments/dev
cp backend.hcl.example backend.hcl            # S3 state: versioned, SSE-KMS, native locking
cp terraform.tfvars.example terraform.tfvars  # identifiers only; no secrets
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan && terraform apply tfplan
```

Then, once:

1. **Create the service roles and databases.** The command comes from the
   `run_task_commands` output. Check `/ecs/mcpgw-dev/db-bootstrap` for "service roles and
   databases are in place".

   ```bash
   eval "$(terraform output -json run_task_commands | jq -r '."db-bootstrap"')"
   ```

2. **Restart the gateway if needed.** If the gateway's first deployment failed before the
   roles existed (the circuit breaker rolls it back), restart it:
   `aws ecs update-service --cluster mcpgw-dev-cluster --service gateway --force-new-deployment`.
3. **Resolve the hostname inside the VPC.** If Terraform created the record, associate the
   private hosted zone with the VPC (`aws route53 associate-vpc-with-hosted-zone`) so that
   in-VPC clients resolve `gateway_hostname`. Otherwise point your DNS at the `alb_dns_name`
   output.
4. **For agent runs, set the two external secrets.** Read the values from files, never
   from command-line arguments:

   ```bash
   aws secretsmanager put-secret-value --secret-id mcpgw-dev/anthropic-api-key --secret-string file://anthropic.key
   aws secretsmanager put-secret-value --secret-id mcpgw-dev/agent-gateway-token --secret-string file://session.jwt
   ```

   Then start a run by appending
   `--overrides '{"containerOverrides":[{"name":"agent-runtime","command":["run","<task>","--wait"]}]}'`
   to the `agent-runtime` run-task command. The token is a session token issued by your
   identity provider; its `sid` becomes the thread id. In production, replace the stored
   token with your identity provider's token exchange.

**Rotate a generated secret** by bumping its `version` in `main.tf` and applying, then:

| Secret | After applying |
| --- | --- |
| Database passwords | Re-run db-bootstrap, then force a new deployment of the gateway. |
| Redis AUTH token | Rotated in the same apply with the `ROTATE` strategy, so old and new tokens both work. Force a new deployment of the gateway. |
| Upstream assertion key | Force new deployments of the gateway and crm-ops together. Calls fail until both are on the new key. |

**Destroy** by setting `alb_deletion_protection = false` and
`database_deletion_protection = false`, applying, then running `terraform destroy`. RDS
keeps a final snapshot.

## Tests and checks

```bash
make tf-fmt        # terraform fmt -check -recursive
make tf-validate   # init -backend=false + validate (providers downloaded once into a shared cache)
make tf-test       # 33 plan/apply tests: 5 modules + the environment, mocked AWS (no account, no cost)
```

The tests assert the security properties above, among them:
- only the gateway and the console face clients;
- crm-ops admits the gateway alone, and tasks accept no traffic;
- generated secret values never reach state;
- the data tier has no route out;
- TLS-only database and cache, and `noeviction`.

They also cover rejected inputs: images not pinned by digest, plain HTTP without explicit
opt-in, a missing sidecar image, and unknown callers.

Trivy (`trivy config infra/terraform`) reports no findings. Two exceptions are justified
in the code:
- **AWS-0054:** plain HTTP exists only in the `allow_insecure_http` sandbox mode.
- **AWS-0104:** HTTPS egress to the internet, which the identity provider and the Claude
  API need.

## Production hardening

The dev environment trades availability for cost. For production:
- `nat_gateway_mode = "per_az"`, `az_count = 3`, RDS `multi_az = true`.
- **Encrypt east-west traffic:** Service Connect TLS with AWS Private CA. Inside the VPC it
  is otherwise security-group-isolated plain HTTP.
- **Verify the database certificate:** use `GATEWAY_DATABASE_SSL=verify-full`, with the
  RDS CA bundle baked into the images (`require` already encrypts).
- **Filter egress by domain:** add an allow-list at the edge (an egress proxy or AWS
  Network Firewall) in front of the NAT.
- **Log ALB access:** set `alb_access_logs_bucket`.
- **Supply chain:** sign and scan images in CI, and pin by digest (already enforced).
- **Agent identity:** use your identity provider's token exchange instead of the stored
  `agent-gateway-token`.

Main cost drivers in dev:
- interface VPC endpoints (billed per AZ-hour);
- the NAT gateway;
- Fargate tasks: 2 gateway, 2 crm-ops and 2 console replicas, plus sidecars;
- the RDS and ElastiCache nodes.

Sandboxes can pass `interface_endpoints = []` to the `vpc` module, which sends AWS API
traffic through the NAT instead.

Conventions: Terraform >= 1.11, AWS provider 6.x (exact versions in
`.terraform.lock.hcl`), provider `default_tags`, an `allowed_account_ids` guard, and images
pinned by digest.
