variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "allowed_account_ids" {
  description = "AWS account ids this environment may be applied to (guards against applying with the wrong credentials)."
  type        = list(string)

  validation {
    condition     = length(var.allowed_account_ids) > 0 && alltrue([for id in var.allowed_account_ids : can(regex("^[0-9]{12}$", id))])
    error_message = "allowed_account_ids must list 12-digit AWS account ids."
  }
}

variable "project" {
  description = "Short project name used in resource names."
  type        = string
  default     = "mcpgw"
}

variable "environment" {
  description = "Environment name used in resource names and tags."
  type        = string
  default     = "dev"
}

variable "vpc_cidr" {
  description = "IPv4 CIDR of the environment's VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "az_count" {
  description = "Availability Zones to span."
  type        = number
  default     = 2
}

variable "alb_ingress_cidrs" {
  description = "Networks allowed to reach the gateway's load balancer (where agents and operators run)."
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate for the gateway's HTTPS listener."
  type        = string
  default     = null
}

variable "allow_insecure_http" {
  description = "Allow plain HTTP when no certificate is set (throwaway sandboxes only)."
  type        = bool
  default     = false
}

variable "gateway_hostname" {
  description = "DNS name clients use for the gateway and console, covered by certificate_arn (e.g. \"gateway.example.com\"). The in-VPC agent-runtime task connects to it; null falls back to the ALB's own DNS name."
  type        = string
  default     = null
}

variable "private_zone_id" {
  description = "Route 53 private hosted zone (associated with this VPC) in which to create gateway_hostname -> ALB. null: manage that record yourself."
  type        = string
  default     = null
}

variable "approval_webhook_url" {
  description = "Optional receiver for signed approval webhooks (e.g. a chat bridge). It verifies signatures with the generated approval-webhook-hmac-key secret."
  type        = string
  default     = null

  validation {
    condition     = var.approval_webhook_url == null || startswith(coalesce(var.approval_webhook_url, "-"), "https://")
    error_message = "approval_webhook_url must use https."
  }
}

variable "gateway_image" {
  description = "Gateway container image, pinned by digest (<repository>@sha256:<digest>)."
  type        = string
}

variable "crm_ops_image" {
  description = "crm-ops MCP server container image, pinned by digest."
  type        = string
}

variable "operator_ui_image" {
  description = "Operator console container image, pinned by digest."
  type        = string
}

variable "agent_runtime_image" {
  description = "Agent runtime container image, pinned by digest (an on-demand task, not a service)."
  type        = string
}

variable "db_bootstrap_image" {
  description = "PostgreSQL client image for the db-bootstrap task, pinned by digest (postgres:17-alpine provides psql 17)."
  type        = string
}

variable "otel_collector_image" {
  description = "AWS Distro for OpenTelemetry collector sidecar image, pinned by digest."
  type        = string
}

variable "alb_deletion_protection" {
  description = "Protect the load balancer from deletion (set false before destroying the environment)."
  type        = bool
  default     = true
}

variable "database_deletion_protection" {
  description = "Protect the database from deletion (set false before destroying the environment)."
  type        = bool
  default     = true
}

variable "jwt_issuer" {
  description = "Expected `iss` of agent and operator JWTs (the enterprise IdP)."
  type        = string
}

variable "jwt_audience" {
  description = "Expected `aud` of JWTs presented to the gateway."
  type        = string
  default     = "mcp-gateway"
}

variable "jwt_jwks_url" {
  description = "IdP JWKS endpoint used to verify RS256/ES256 token signatures (no shared signing secret in AWS)."
  type        = string

  validation {
    condition     = startswith(var.jwt_jwks_url, "https://")
    error_message = "jwt_jwks_url must use https."
  }
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
