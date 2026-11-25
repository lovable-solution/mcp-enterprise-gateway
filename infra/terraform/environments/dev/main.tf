# -----------------------------------------------------------------------------
# Environment: dev
#
# Composition only: every resource lives in a module. Trust topology:
#   alb_ingress_cidrs -> ALB -> gateway   (the only service behind the ALB)
#   gateway -> crm-ops                    (Service Connect; crm-ops admits the
#                                          gateway's security group only)
#   gateway -> RDS :5432, ElastiCache :6379
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
}

module "vpc" {
  source = "../../modules/vpc"

  name             = local.name
  cidr_block       = var.vpc_cidr
  az_count         = var.az_count
  nat_gateway_mode = "single" # production: "per_az"
}

module "secrets" {
  source = "../../modules/secrets_manager"

  name = local.name

  generated_secrets = {
    "gateway-db-password" = {
      description = "Password of the gateway's Postgres role (created by the migration task)."
    }
    "redis-auth-token" = {
      description = "ElastiCache AUTH token."
      length      = 64
    }
    "upstream-assertion-key" = {
      description = "HS256 key: the gateway signs identity assertions, MCP servers verify them."
      length      = 64
    }
  }

  external_secrets = {
    "approval-webhook-hmac-key" = {
      description = "HMAC-SHA256 key that signs approval webhooks. Set its value after the first apply."
    }
  }
}

module "rds" {
  source = "../../modules/rds_postgres"

  name       = local.name
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_data_subnet_ids
  allowed_security_group_ids = {
    gateway = module.ecs.service_security_group_ids["gateway"]
  }
  multi_az = false # production: true
}

module "redis" {
  source = "../../modules/elasticache_redis"

  name       = local.name
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_data_subnet_ids
  allowed_security_group_ids = {
    gateway = module.ecs.service_security_group_ids["gateway"]
  }
  auth_token_secret_arn = module.secrets.secret_arns["redis-auth-token"]
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
  secrets_kms_key_arn       = module.secrets.kms_key_arn
  service_connect_namespace = local.service_connect_namespace

  services = {
    gateway = {
      image          = var.gateway_image
      container_port = local.gateway_port
      expose_via_alb = true
      # Setting names are the contract with the gateway's runtime configuration
      # (defined in Phase 2). The policy file is baked into the image, so every
      # policy change ships through review and a deployment.
      environment = {
        GATEWAY_ENVIRONMENT   = var.environment
        GATEWAY_POLICY_PATH   = "/app/config/policies.yaml"
        GATEWAY_DATABASE_HOST = module.rds.address
        GATEWAY_DATABASE_PORT = tostring(module.rds.port)
        GATEWAY_DATABASE_NAME = module.rds.database_name
        GATEWAY_DATABASE_USER = "mcp_gateway"
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
        # request is authenticated and arrives through the ALB, so the check is off here.
        GATEWAY_ALLOWED_HOSTS       = jsonencode([])
        OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318" # ADOT sidecar
      }
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
      environment = {
        CRM_OPS_ENVIRONMENT = var.environment
        CRM_OPS_ALLOWED_HOSTS = jsonencode([
          "crm-ops.${local.service_connect_namespace}",
          "crm-ops.${local.service_connect_namespace}:*",
        ])
        OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318" # ADOT sidecar
      }
      secrets = {
        CRM_OPS_GATEWAY_ASSERTION_KEY = module.secrets.secret_arns["upstream-assertion-key"]
      }
    }
  }
}
