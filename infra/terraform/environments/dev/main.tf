# -----------------------------------------------------------------------------
# Environment: dev
#
# Composition only: every resource lives in a module. Trust topology:
#   alb_ingress_cidrs -> ALB -> gateway       /mcp, /v1/*, /healthz, /readyz
#                            -> operator-ui   everything else (the console)
#   operator-ui -> gateway                    (Service Connect: the console's BFF)
#   gateway -> crm-ops                        (Service Connect; crm-ops admits the
#                                              gateway's security group only)
#   gateway -> RDS :5432, ElastiCache :6379 (TLS)
#   agent-runtime task -> ALB -> gateway      (like any agent), and RDS :5432
#   db-bootstrap task -> RDS :5432            (the only reader of the master secret)
# Secrets reach tasks only as ECS `secrets` (Secrets Manager valueFrom).
# -----------------------------------------------------------------------------

locals {
  name = "${var.project}-${var.environment}"

  tags = merge(var.tags, {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Repository  = "mcp-enterprise-gateway"
  })

  service_connect_namespace = "mcp.internal"
  gateway_port              = 8000
  crm_ops_port              = 8001
  console_port              = 3000

  https            = var.certificate_arn != null
  public_host      = coalesce(var.gateway_hostname, module.ecs.alb_dns_name)
  public_url       = "${local.https ? "https" : "http"}://${local.public_host}"
  gateway_internal = "http://gateway.${local.service_connect_namespace}:${local.gateway_port}"

  databases = {
    gateway = { name = "mcp_gateway", user = "mcp_gateway" }
    agent   = { name = "agent_runtime", user = "agent_runtime" }
  }

  otel_endpoint = { OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318" } # ADOT sidecar
}

module "vpc" {
  source = "../../modules/vpc"

  name             = local.name
  cidr_block       = var.vpc_cidr
  az_count         = var.az_count
  nat_gateway_mode = "single" # production: "per_az"
  logs_kms_key_arn = module.secrets.kms_key_arn
}

module "secrets" {
  source = "../../modules/secrets_manager"

  name = local.name

  generated_secrets = {
    "gateway-db-password" = {
      description = "Password of the gateway's Postgres role (set by the db-bootstrap task)."
    }
    "agent-db-password" = {
      description = "Password of the agent runtime's Postgres role (LangGraph checkpoints)."
    }
    "redis-auth-token" = {
      description = "ElastiCache AUTH token."
      length      = 64
    }
    "upstream-assertion-key" = {
      description = "HS256 key: the gateway signs identity assertions, MCP servers verify them."
      length      = 64
    }
    "approval-webhook-hmac-key" = {
      description = "HMAC-SHA256 key signing approval webhooks; receivers read it here to verify."
      length      = 64
    }
    "console-session-secret" = {
      description = "Encrypts the operator console's session cookie."
      length      = 64
    }
  }

  # Values set out-of-band after the first apply; only the on-demand agent task
  # uses them, so nothing fails to start while they are empty.
  external_secrets = {
    "anthropic-api-key" = {
      description = "Claude API key for the agent runtime."
    }
    "agent-gateway-token" = {
      description = "IdP-issued gateway token (one session) for the agent runtime task."
    }
  }
}

module "rds" {
  source = "../../modules/rds_postgres"

  name          = local.name
  vpc_id        = module.vpc.vpc_id
  subnet_ids    = module.vpc.private_data_subnet_ids
  database_name = local.databases.gateway.name
  kms_key_arn   = module.secrets.kms_key_arn
  allowed_security_group_ids = {
    gateway       = module.ecs.service_security_group_ids["gateway"]
    agent-runtime = module.ecs.task_security_group_ids["agent-runtime"]
    db-bootstrap  = module.ecs.task_security_group_ids["db-bootstrap"]
  }
  multi_az            = false # production: true
  deletion_protection = var.database_deletion_protection
}

module "redis" {
  source = "../../modules/elasticache_redis"

  name        = local.name
  vpc_id      = module.vpc.vpc_id
  subnet_ids  = module.vpc.private_data_subnet_ids
  kms_key_arn = module.secrets.kms_key_arn
  allowed_security_group_ids = {
    gateway = module.ecs.service_security_group_ids["gateway"]
  }
  auth_token_secret_arn = module.secrets.secret_arns["redis-auth-token"]
  auth_token_version    = module.secrets.secret_versions["redis-auth-token"]
  logs_kms_key_arn      = module.secrets.kms_key_arn
}

module "ecs" {
  source = "../../modules/ecs_fargate"

  name                      = local.name
  vpc_id                    = module.vpc.vpc_id
  alb_subnet_ids            = module.vpc.private_app_subnet_ids # internal ALB
  service_subnet_ids        = module.vpc.private_app_subnet_ids
  internal_alb              = true
  alb_ingress_cidrs         = var.alb_ingress_cidrs
  certificate_arn           = var.certificate_arn
  allow_insecure_http       = var.allow_insecure_http
  alb_deletion_protection   = var.alb_deletion_protection
  alb_aliases               = var.gateway_hostname != null && var.private_zone_id != null ? { (var.gateway_hostname) = var.private_zone_id } : {}
  secrets_kms_key_arn       = module.secrets.kms_key_arn
  logs_kms_key_arn          = module.secrets.kms_key_arn
  service_connect_namespace = local.service_connect_namespace
  otel_sidecar_image        = var.otel_collector_image

  services = {
    gateway = {
      image                 = var.gateway_image
      container_port        = local.gateway_port
      desired_count         = 2
      max_count             = 6
      expose_via_alb        = true
      alb_path_patterns     = ["/mcp", "/mcp/*", "/v1/*", "/healthz", "/readyz"]
      alb_priority          = 100
      ingress_from_services = ["operator-ui"] # the console's backend-for-frontend
      egress_to = {
        postgres = { security_group_id = module.rds.security_group_id, port = module.rds.port }
        redis    = { security_group_id = module.redis.security_group_id, port = module.redis.port }
      }
      stop_timeout = 60 # lets approved calls in flight finish (the gateway waits up to 30 s)
      # Setting names are the contract with the gateway's runtime configuration. The
      # policy file is baked into the image, so every policy change ships through
      # review and a deployment.
      environment = merge(local.otel_endpoint, {
        GATEWAY_ENVIRONMENT   = var.environment
        GATEWAY_POLICY_PATH   = "/app/config/policies.yaml"
        GATEWAY_DATABASE_HOST = module.rds.address
        GATEWAY_DATABASE_PORT = tostring(module.rds.port)
        GATEWAY_DATABASE_NAME = local.databases.gateway.name
        GATEWAY_DATABASE_USER = local.databases.gateway.user
        GATEWAY_DATABASE_SSL  = "require"
        GATEWAY_REDIS_HOST    = module.redis.primary_endpoint_address
        GATEWAY_REDIS_PORT    = tostring(module.redis.port)
        GATEWAY_REDIS_TLS     = "true"
        GATEWAY_UPSTREAMS = jsonencode({
          "crm-ops" = "http://crm-ops.${local.service_connect_namespace}:${local.crm_ops_port}/mcp"
        })
        GATEWAY_JWT_ISSUER     = var.jwt_issuer
        GATEWAY_JWT_AUDIENCE   = var.jwt_audience
        GATEWAY_JWT_JWKS_URL   = var.jwt_jwks_url
        GATEWAY_JWT_ALGORITHMS = jsonencode(["RS256", "ES256"])
        # DNS-rebinding protection guards unauthenticated local servers; every gateway
        # request is authenticated and arrives through the ALB or Service Connect.
        GATEWAY_ALLOWED_HOSTS = jsonencode([])
        GATEWAY_WEBHOOK_URL   = var.approval_webhook_url == null ? "" : var.approval_webhook_url # empty: no webhooks
      })
      secrets = {
        GATEWAY_DATABASE_PASSWORD    = module.secrets.secret_arns["gateway-db-password"]
        GATEWAY_REDIS_PASSWORD       = module.secrets.secret_arns["redis-auth-token"]
        GATEWAY_WEBHOOK_HMAC_KEY     = module.secrets.secret_arns["approval-webhook-hmac-key"]
        GATEWAY_UPSTREAM_SIGNING_KEY = module.secrets.secret_arns["upstream-assertion-key"]
      }
    }

    crm-ops = {
      image                 = var.crm_ops_image
      container_port        = local.crm_ops_port
      ingress_from_services = ["gateway"] # never reachable from the ALB or from agents
      environment = merge(local.otel_endpoint, {
        CRM_OPS_ENVIRONMENT = var.environment
        CRM_OPS_ALLOWED_HOSTS = jsonencode([
          "crm-ops.${local.service_connect_namespace}",
          "crm-ops.${local.service_connect_namespace}:*",
        ])
      })
      secrets = {
        CRM_OPS_GATEWAY_ASSERTION_KEY = module.secrets.secret_arns["upstream-assertion-key"]
      }
    }

    operator-ui = {
      image             = var.operator_ui_image
      container_port    = local.console_port
      cpu               = 256
      memory            = 512
      health_check_path = "/api/health"
      expose_via_alb    = true
      alb_path_patterns = ["/*"]
      alb_priority      = 1000 # the catch-all: evaluated after the gateway's paths
      writable_paths    = ["/tmp", "/app/.next/cache"]
      otel_sidecar      = false # the console emits no telemetry of its own
      environment = {
        GATEWAY_URL   = local.gateway_internal # server-side only (Service Connect)
        DEV_SIGNIN    = "false"                # identity-provider tokens only
        COOKIE_SECURE = local.https ? "true" : "false"
      }
      secrets = {
        SESSION_SECRET = module.secrets.secret_arns["console-session-secret"]
      }
    }
  }

  tasks = {
    # Creates the service roles and databases (idempotent; re-run after rotating the
    # database passwords). The only workload that can read the RDS master secret.
    db-bootstrap = {
      image   = var.db_bootstrap_image
      user    = "70:70" # postgres user of the alpine image
      command = ["sh", "-c", file("${path.module}/db-bootstrap.sh")]
      egress_to = {
        postgres = { security_group_id = module.rds.security_group_id, port = module.rds.port }
      }
      environment = {
        PGHOST            = module.rds.address
        PGPORT            = tostring(module.rds.port)
        PGDATABASE        = "postgres"
        PGSSLMODE         = "require"
        PGCONNECT_TIMEOUT = "10"
        PGAPPNAME         = "db-bootstrap"
        GATEWAY_DB_NAME   = local.databases.gateway.name
        GATEWAY_DB_USER   = local.databases.gateway.user
        AGENT_DB_NAME     = local.databases.agent.name
        AGENT_DB_USER     = local.databases.agent.user
      }
      secrets = {
        PGUSER              = "${module.rds.master_user_secret_arn}:username::"
        PGPASSWORD          = "${module.rds.master_user_secret_arn}:password::"
        GATEWAY_DB_PASSWORD = module.secrets.secret_arns["gateway-db-password"]
        AGENT_DB_PASSWORD   = module.secrets.secret_arns["agent-db-password"]
      }
    }

    # One governed agent run per task: override the command with
    #   ["run", "<task for the agent>", "--wait"]
    # It reaches tools only through the load balancer and the gateway, like any agent.
    agent-runtime = {
      image        = var.agent_runtime_image
      cpu          = 512
      memory       = 1024
      alb_access   = true
      otel_sidecar = true
      egress_to = {
        postgres = { security_group_id = module.rds.security_group_id, port = module.rds.port }
      }
      environment = merge(local.otel_endpoint, {
        AGENT_ENVIRONMENT     = var.environment
        AGENT_GATEWAY_URL     = local.public_url
        AGENT_CHECKPOINTER    = "postgres"
        AGENT_DATABASE_HOST   = module.rds.address
        AGENT_DATABASE_PORT   = tostring(module.rds.port)
        AGENT_DATABASE_NAME   = local.databases.agent.name
        AGENT_DATABASE_USER   = local.databases.agent.user
        AGENT_DATABASE_SSL    = "require"
        PYDANTIC_AI_NO_BANNER = "1"
      })
      secrets = {
        ANTHROPIC_API_KEY       = module.secrets.secret_arns["anthropic-api-key"]
        AGENT_GATEWAY_TOKEN     = module.secrets.secret_arns["agent-gateway-token"]
        AGENT_DATABASE_PASSWORD = module.secrets.secret_arns["agent-db-password"]
      }
    }
  }
}

# The in-VPC agent task verifies the gateway's certificate, so it must call the
# gateway by a name that certificate covers.
check "agent_task_tls_name" {
  assert {
    condition     = var.certificate_arn == null || var.gateway_hostname != null
    error_message = "certificate_arn is set but gateway_hostname is not: the agent-runtime task would call the ALB by its AWS DNS name, which the certificate does not cover. Set gateway_hostname (and private_zone_id to create the record)."
  }
}
