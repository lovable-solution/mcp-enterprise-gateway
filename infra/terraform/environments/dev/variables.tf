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

variable "gateway_image" {
  description = "Gateway container image, pinned by digest (<repository>@sha256:<digest>)."
  type        = string
}

variable "crm_ops_image" {
  description = "crm-ops MCP server container image, pinned by digest."
  type        = string
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
