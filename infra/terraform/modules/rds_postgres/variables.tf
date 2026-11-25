variable "name" {
  description = "Name prefix and DB instance identifier stem, e.g. \"mcpgw-dev\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "name must be 3-20 lowercase letters, digits or hyphens and start with a letter."
  }
}

variable "vpc_id" {
  description = "VPC to place the database in."
  type        = string
}

variable "subnet_ids" {
  description = "Private-data subnets (one per AZ) for the DB subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to connect on the Postgres port, keyed by a static client name (e.g. { gateway = sg-... }). Static keys keep for_each plannable before the IDs exist."
  type        = map(string)
  default     = {}
}

variable "engine_version" {
  description = "PostgreSQL major version; minor versions are upgraded automatically in the maintenance window."
  type        = string
  default     = "17"
}

variable "instance_class" {
  description = "DB instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "allocated_storage_gb" {
  description = "Initial gp3 storage (GiB)."
  type        = number
  default     = 20
}

variable "max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling (GiB)."
  type        = number
  default     = 100

  validation {
    condition     = var.max_allocated_storage_gb >= var.allocated_storage_gb
    error_message = "max_allocated_storage_gb must be at least allocated_storage_gb."
  }
}

variable "multi_az" {
  description = "Synchronous standby in a second AZ (true for production)."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Automated backup retention (1-35 days)."
  type        = number
  default     = 7

  validation {
    condition     = var.backup_retention_days >= 1 && var.backup_retention_days <= 35
    error_message = "backup_retention_days must be between 1 and 35."
  }
}

variable "deletion_protection" {
  description = "Block deletion of the instance (disable deliberately, never by default)."
  type        = bool
  default     = true
}

variable "database_name" {
  description = "Initial database: the gateway's approval store."
  type        = string
  default     = "mcp_gateway"
}

variable "master_username" {
  description = "Master user name. Its password is generated, stored and rotated by RDS in Secrets Manager (manage_master_user_password)."
  type        = string
  default     = "mcpgw_admin"
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for storage, the master secret and performance-monitoring data (null: AWS-managed keys)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags for this module's resources (provider default_tags already apply)."
  type        = map(string)
  default     = {}
}
