variable "name" {
  description = "Name prefix for every resource in this module, e.g. \"mcpgw-dev\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "name must be 3-20 lowercase letters, digits or hyphens and start with a letter."
  }
}

variable "cidr_block" {
  description = "IPv4 CIDR of the VPC, split into public, private-app and private-data tiers in every AZ."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.cidr_block)) && tonumber(split("/", var.cidr_block)[1]) <= 20
    error_message = "cidr_block must be a valid IPv4 CIDR of /20 or larger."
  }
}

variable "az_count" {
  description = "Availability Zones to span: 2 for dev, 3 for production."
  type        = number
  default     = 2

  validation {
    condition     = contains([2, 3], var.az_count)
    error_message = "az_count must be 2 or 3."
  }
}

variable "nat_gateway_mode" {
  description = "\"single\": one shared NAT gateway (cheaper). \"per_az\": one per AZ (no cross-AZ dependency)."
  type        = string
  default     = "single"

  validation {
    condition     = contains(["single", "per_az"], var.nat_gateway_mode)
    error_message = "nat_gateway_mode must be \"single\" or \"per_az\"."
  }
}

variable "interface_endpoints" {
  description = "AWS services reached through interface VPC endpoints rather than the NAT gateway (image pulls, secrets, logs, STS, KMS). An S3 gateway endpoint is always created for ECR layers."
  type        = set(string)
  default     = ["ecr.api", "ecr.dkr", "secretsmanager", "logs", "sts", "kms"]
}

variable "flow_logs_retention_days" {
  description = "Retention of VPC flow logs (CloudWatch Logs). 0 disables flow logs."
  type        = number
  default     = 90

  validation {
    condition     = contains([0, 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.flow_logs_retention_days)
    error_message = "flow_logs_retention_days must be 0 or a retention period CloudWatch Logs supports."
  }
}

variable "logs_kms_key_arn" {
  description = "KMS key for this module's CloudWatch log group (null: AWS-managed encryption). Its key policy must allow the CloudWatch Logs service principal (the secrets_manager module's key does)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags for this module's resources (provider default_tags already apply)."
  type        = map(string)
  default     = {}
}
