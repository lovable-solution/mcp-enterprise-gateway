variable "name" {
  description = "Name prefix and replication group id stem, e.g. \"mcpgw-dev\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "name must be 3-20 lowercase letters, digits or hyphens and start with a letter."
  }
}

variable "vpc_id" {
  description = "VPC to place the cache in."
  type        = string
}

variable "subnet_ids" {
  description = "Private-data subnets (one per AZ) for the cache subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to connect on the Redis port, keyed by a static client name (e.g. { gateway = sg-... })."
  type        = map(string)
  default     = {}
}

variable "engine" {
  description = "\"redis\" (Redis OSS) or \"valkey\" (wire-compatible, lower ElastiCache price). The gateway uses only commands both support."
  type        = string
  default     = "redis"

  validation {
    condition     = contains(["redis", "valkey"], var.engine)
    error_message = "engine must be \"redis\" or \"valkey\"."
  }
}

variable "engine_version" {
  description = "Engine version. The local compose stack pins a Redis release no newer than this feature set."
  type        = string
  default     = "7.1"
}

variable "node_type" {
  description = "Cache node type."
  type        = string
  default     = "cache.t4g.small"
}

variable "num_cache_clusters" {
  description = "Nodes in the replication group: 1 primary + replicas. 2 or more enables Multi-AZ automatic failover."
  type        = number
  default     = 2

  validation {
    condition     = var.num_cache_clusters >= 1 && var.num_cache_clusters <= 6
    error_message = "num_cache_clusters must be between 1 and 6."
  }
}

variable "auth_token_secret_arn" {
  description = "Secrets Manager ARN holding the AUTH token (a generated secret from the secrets_manager module). Read ephemerally and passed write-only, so the token never enters plans or state. Clients connect over TLS only."
  type        = string
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for at-rest encryption (null: AWS-managed key)."
  type        = string
  default     = null
}

variable "snapshot_retention_days" {
  description = "Daily snapshot retention. Budget ledgers are short-lived, so 1 day suffices for most environments."
  type        = number
  default     = 1
}

variable "tags" {
  description = "Extra tags for this module's resources (provider default_tags already apply)."
  type        = map(string)
  default     = {}
}
