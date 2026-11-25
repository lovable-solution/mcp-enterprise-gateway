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
  description = "ACM certificate for the HTTPS listener (TLS 1.3/1.2 forward-secrecy policy; no plaintext listener). null serves plain HTTP, refused unless allow_insecure_http is set."
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

variable "alb_deletion_protection" {
  description = "Block deletion of the load balancer (turn off deliberately before destroying an environment)."
  type        = bool
  default     = true
}

variable "alb_access_logs_bucket" {
  description = "S3 bucket for ALB access logs (null disables them). The bucket policy must allow the regional ELB log-delivery principal."
  type        = string
  default     = null
}

variable "alb_aliases" {
  description = "Route 53 alias records to create for the ALB: fully qualified name => hosted zone id. Use a private zone associated with the VPC so in-VPC clients (the agent-runtime task) resolve the name the certificate covers."
  type        = map(string)
  default     = {}
}

variable "services" {
  description = <<-EOT
    Long-running Fargate services keyed by name, which is also the service's Service
    Connect DNS name (<name>.<service_connect_namespace>). Only services with
    expose_via_alb = true are reachable through the load balancer (matched by
    alb_path_patterns, lowest alb_priority first); everything else is east-west
    only, from the services listed in ingress_from_services. That is what stops
    agents from calling MCP servers directly: crm-ops admits traffic from the
    gateway's security group and nothing else.

    secrets maps an environment variable to a Secrets Manager ARN (optionally
    "<arn>:<json-key>::"); ECS injects it at start-up, so values never appear in
    task definitions. Each workload gets its own execution role that can read only
    its own secrets.

    Egress is least-privilege: HTTPS anywhere (AWS APIs, the IdP's JWKS), the
    services it may call, and the targets listed in egress_to (data stores).
  EOT
  type = map(object({
    image                    = string
    container_port           = number
    cpu                      = optional(number, 512)
    memory                   = optional(number, 1024)
    desired_count            = optional(number, 2)
    max_count                = optional(number) # enables CPU target tracking up to this count
    health_check_path        = optional(string, "/healthz")
    expose_via_alb           = optional(bool, false)
    alb_path_patterns        = optional(list(string), ["/*"])
    alb_priority             = optional(number) # lower wins; default 100 + 10 * position
    ingress_from_services    = optional(list(string), [])
    egress_to                = optional(map(object({ security_group_id = string, port = number })), {})
    environment              = optional(map(string), {})
    secrets                  = optional(map(string), {})
    command                  = optional(list(string))
    user                     = optional(string) # "uid:gid"; null keeps the image's (non-root) USER
    task_role_policy_json    = optional(string)
    readonly_root_filesystem = optional(bool, true)
    writable_paths           = optional(list(string), ["/tmp"])
    otel_sidecar             = optional(bool, true)
    stop_timeout             = optional(number, 30)
  }))

  validation {
    condition     = alltrue([for key in keys(var.services) : can(regex("^[a-z][a-z0-9-]{0,20}$", key))])
    error_message = "service names must be 1-21 lowercase letters, digits or hyphens and start with a letter."
  }

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

  validation {
    condition     = alltrue([for svc in values(var.services) : svc.max_count == null || try(svc.max_count >= svc.desired_count, false)])
    error_message = "max_count must be at least desired_count."
  }

  validation {
    condition     = alltrue([for svc in values(var.services) : !svc.expose_via_alb || (length(svc.alb_path_patterns) > 0 && alltrue([for p in svc.alb_path_patterns : startswith(p, "/")]))])
    error_message = "services exposed via the ALB need alb_path_patterns, each starting with \"/\"."
  }

  validation {
    condition = length([for svc in values(var.services) : svc.alb_priority if svc.expose_via_alb && svc.alb_priority != null]) == length(distinct([
      for svc in values(var.services) : svc.alb_priority if svc.expose_via_alb && svc.alb_priority != null
    ]))
    error_message = "alb_priority values must be unique among exposed services."
  }

  validation {
    condition     = alltrue(flatten([for svc in values(var.services) : [for path in svc.writable_paths : startswith(path, "/")]]))
    error_message = "writable_paths must be absolute paths."
  }
}

variable "tasks" {
  description = <<-EOT
    On-demand Fargate task definitions (no service): one-off jobs such as the
    database bootstrap or an agent run, started with `aws ecs run-task` (see the
    run_task_commands output). They accept no inbound traffic; alb_access lets a
    task reach the load balancer like any other client (an agent going through
    the gateway). Same secret, role and logging plumbing as services.
  EOT
  type = map(object({
    image                    = string
    cpu                      = optional(number, 256)
    memory                   = optional(number, 512)
    command                  = optional(list(string))
    user                     = optional(string) # "uid:gid"; null keeps the image's USER
    environment              = optional(map(string), {})
    secrets                  = optional(map(string), {})
    egress_to                = optional(map(object({ security_group_id = string, port = number })), {})
    alb_access               = optional(bool, false)
    task_role_policy_json    = optional(string)
    readonly_root_filesystem = optional(bool, true)
    writable_paths           = optional(list(string), ["/tmp"])
    otel_sidecar             = optional(bool, false)
  }))
  default = {}

  validation {
    condition     = alltrue([for key in keys(var.tasks) : can(regex("^[a-z][a-z0-9-]{0,20}$", key)) && !contains(keys(var.services), key)])
    error_message = "task names must be 1-21 lowercase letters, digits or hyphens, and differ from every service name."
  }

  validation {
    condition     = alltrue([for task in values(var.tasks) : can(regex("@sha256:[0-9a-f]{64}$", task.image))])
    error_message = "task images must be pinned by digest (<repository>@sha256:<64 hex>)."
  }

  validation {
    condition     = alltrue([for task in values(var.tasks) : contains([256, 512, 1024, 2048, 4096, 8192, 16384], task.cpu)])
    error_message = "cpu must be a Fargate CPU size (256, 512, 1024, 2048, 4096, 8192 or 16384)."
  }
}

variable "secrets_kms_key_arn" {
  description = "KMS key encrypting the referenced secrets; each execution role gets kms:Decrypt on it."
  type        = string
}

variable "service_connect_namespace" {
  description = "Cloud Map namespace used by ECS Service Connect for east-west calls (gateway -> MCP servers, console -> gateway)."
  type        = string
  default     = "mcp.internal"
}

variable "enable_otel_sidecar" {
  description = "Run an AWS Distro for OpenTelemetry collector sidecar in each workload that asks for one; services export OTLP to localhost:4317/4318 and the sidecar forwards traces to X-Ray and metrics to CloudWatch."
  type        = bool
  default     = true
}

variable "otel_sidecar_image" {
  description = "ADOT collector image, pinned by digest (public.ecr.aws/aws-observability/aws-otel-collector@sha256:...). Required while enable_otel_sidecar is true."
  type        = string
  default     = null

  validation {
    condition     = var.otel_sidecar_image == null || can(regex("@sha256:[0-9a-f]{64}$", var.otel_sidecar_image))
    error_message = "otel_sidecar_image must be pinned by digest."
  }

  validation {
    condition     = !var.enable_otel_sidecar || var.otel_sidecar_image != null
    error_message = "otel_sidecar_image is required while enable_otel_sidecar is true."
  }
}

variable "cpu_architecture" {
  description = "Task CPU architecture: X86_64, or ARM64 (Graviton, cheaper) when every image is multi-arch."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for container logs."
  type        = number
  default     = 30
}

variable "logs_kms_key_arn" {
  description = "KMS key for CloudWatch log groups (null: AWS-managed encryption). Its key policy must allow the CloudWatch Logs service principal."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags for this module's resources (provider default_tags already apply)."
  type        = map(string)
  default     = {}
}
