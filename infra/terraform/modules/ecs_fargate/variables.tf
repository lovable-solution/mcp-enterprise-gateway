variable "name" {
  description = "Name prefix for the cluster, load balancer and services, e.g. \"mcpgw-dev\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "name must be 3-20 lowercase letters, digits or hyphens and start with a letter."
  }
}

variable "vpc_id" {
  description = "VPC hosting the cluster."
  type        = string
}

variable "alb_subnet_ids" {
  description = "Subnets for the ALB: private-app subnets when internal (default), public subnets when internet-facing."
  type        = list(string)
}

variable "service_subnet_ids" {
  description = "Private-app subnets for tasks. Tasks never receive public IPs."
  type        = list(string)
}

variable "internal_alb" {
  description = "Keep the ALB internal (reachable only from inside the VPC or connected networks)."
  type        = bool
  default     = true
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the ALB listeners (agent subnets, VPN, peered networks). No default: exposure is always an explicit decision."
  type        = list(string)

  validation {
    condition     = length(var.alb_ingress_cidrs) > 0 && alltrue([for c in var.alb_ingress_cidrs : can(cidrnetmask(c))])
    error_message = "alb_ingress_cidrs must be a non-empty list of IPv4 CIDRs."
  }
}

variable "certificate_arn" {
  description = "ACM certificate for the HTTPS listener (TLS 1.3 policy; HTTP redirects to HTTPS). null serves plain HTTP, refused unless allow_insecure_http is set."
  type        = string
  default     = null
}

variable "allow_insecure_http" {
  description = "Permit a plain-HTTP listener when no certificate is given. Throwaway sandboxes only: bearer tokens would cross the network in clear text."
  type        = bool
  default     = false

  validation {
    condition     = var.certificate_arn != null || var.allow_insecure_http
    error_message = "certificate_arn is required unless allow_insecure_http = true."
  }
}

variable "alb_idle_timeout_seconds" {
  description = "ALB idle timeout. Must exceed the policy's approvals.inline_wait_seconds and the SSE keep-alive interval, or held tool calls and event streams are cut."
  type        = number
  default     = 300

  validation {
    condition     = var.alb_idle_timeout_seconds >= 60 && var.alb_idle_timeout_seconds <= 4000
    error_message = "alb_idle_timeout_seconds must be between 60 and 4000."
  }
}

variable "services" {
  description = <<-EOT
    Fargate services keyed by name, which is also the service's Service Connect
    DNS name (<name>.<service_connect_namespace>). Only services with
    expose_via_alb = true are reachable from outside the cluster; all others are
    east-west only, from the services listed in their ingress_from_services. That
    is what stops agents from calling MCP servers directly: crm-ops admits
    traffic from the gateway's security group and nothing else.

    secrets maps an environment variable to a Secrets Manager ARN (optionally
    "<arn>:<json-key>::"); ECS injects it at start-up, so values never appear in
    task definitions. Each service gets its own execution role that can read
    only its own secrets.
  EOT
  type = map(object({
    image                    = string
    container_port           = number
    cpu                      = optional(number, 512)
    memory                   = optional(number, 1024)
    desired_count            = optional(number, 2)
    health_check_path        = optional(string, "/healthz")
    expose_via_alb           = optional(bool, false)
    alb_path_patterns        = optional(list(string), ["/*"])
    ingress_from_services    = optional(list(string), [])
    environment              = optional(map(string), {})
    secrets                  = optional(map(string), {})
    task_role_policy_json    = optional(string)
    readonly_root_filesystem = optional(bool, true)
  }))

  validation {
    condition = alltrue(flatten([
      for svc in values(var.services) : [for src in svc.ingress_from_services : contains(keys(var.services), src)]
    ]))
    error_message = "ingress_from_services may only name services defined in var.services."
  }

  validation {
    condition     = alltrue([for svc in values(var.services) : contains([256, 512, 1024, 2048, 4096, 8192, 16384], svc.cpu)])
    error_message = "cpu must be a Fargate CPU size (256, 512, 1024, 2048, 4096, 8192 or 16384)."
  }

  validation {
    condition     = alltrue([for svc in values(var.services) : can(regex("@sha256:[0-9a-f]{64}$", svc.image))])
    error_message = "images must be pinned by digest (<repository>@sha256:<64 hex>) so deployments are immutable."
  }
}

variable "secrets_kms_key_arn" {
  description = "KMS key encrypting the referenced secrets; each execution role gets kms:Decrypt on it."
  type        = string
}

variable "service_connect_namespace" {
  description = "Cloud Map namespace used by ECS Service Connect for east-west calls (gateway -> MCP servers)."
  type        = string
  default     = "mcp.internal"
}

variable "enable_otel_sidecar" {
  description = "Run an AWS Distro for OpenTelemetry collector sidecar in each task; services export OTLP to localhost:4317 and the sidecar forwards traces to X-Ray and metrics to CloudWatch."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for container logs."
  type        = number
  default     = 30
}

variable "logs_kms_key_arn" {
  description = "KMS key for CloudWatch log groups (null: AWS-managed encryption)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags for this module's resources (provider default_tags already apply)."
  type        = map(string)
  default     = {}
}
