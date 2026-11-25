# Terraform (AWS)

Deploys the gateway and the MCP servers as private ECS Fargate services behind an
ALB, with RDS PostgreSQL, ElastiCache and Secrets Manager.

> **Phase 1 status: interface scaffold.** Every module has its complete input
> contract (`variables.tf`, typed and validated) and output contract
> (`outputs.tf`). Outputs that depend on resources are `null` placeholders, and the
> planned resources are listed at the top of each `main.tf`. The `dev` environment is
> wired exactly as it will be in Phase 4, and `terraform validate` passes.

## Layout

```
environments/dev/      root module: composition only (no resources of its own)
modules/
  vpc/                 3 tiers per AZ: public | private-app | private-data, VPC endpoints
  secrets_manager/     CMK + secrets; generated values are write-only (never in state)
  rds_postgres/        approval store; RDS-managed, rotated master credential
  elasticache_redis/   budget ledgers / breaker state; TLS, AUTH, noeviction
  ecs_fargate/         cluster, ALB, per-service SGs, roles, tasks, Service Connect
```

## Trust topology

```
alb_ingress_cidrs ──443──▶ ALB (internal) ──▶ gateway ──Service Connect──▶ crm-ops
                                                 │
                                                 ├──5432──▶ RDS PostgreSQL
                                                 └──6379──▶ ElastiCache (TLS)
```

- Only services with `expose_via_alb = true` get a listener rule: the gateway.
- `crm-ops` admits traffic from the gateway's security group alone, so agents cannot
  reach an MCP server even from inside the VPC.
- Data stores sit in subnets with no internet route; tasks never get public IPs.

## Secrets: nothing in code, tfvars, plans or state

| Secret | Created by | Consumed via |
| --- | --- | --- |
| RDS master credential | RDS (`manage_master_user_password`, auto-rotated) | migration task only |
| Gateway DB password, Redis AUTH token, upstream assertion key | ephemeral `random_password` -> `secret_string_wo` | ECS `secrets` (valueFrom); ElastiCache `auth_token_wo` |
| Approval-webhook HMAC key | out-of-band `put-secret-value` after first apply | ECS `secrets` |
| JWT verification | none: the IdP's public JWKS URL | gateway setting |

The write-only arguments and ephemeral resources this relies on were confirmed in the
locked AWS provider (6.74.0) schema.

## Usage

```bash
make tf-fmt         # terraform fmt -check -recursive
make tf-validate    # init -backend=false + validate (pinned terraform image if none installed)

# real deployments (Phase 4)
cd environments/dev
cp backend.hcl.example backend.hcl            # S3 state: versioned, SSE-KMS, native locking
cp terraform.tfvars.example terraform.tfvars  # identifiers only; no secrets
terraform init -backend-config=backend.hcl && terraform plan
```

Conventions: Terraform >= 1.11, AWS provider 6.x (exact versions in
`.terraform.lock.hcl`), provider `default_tags`, `allowed_account_ids` guard, images
pinned by digest.
