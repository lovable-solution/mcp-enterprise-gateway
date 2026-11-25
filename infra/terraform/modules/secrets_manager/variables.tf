variable "name" {
  description = "Name prefix; secrets are created as \"<name>/<key>\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "name must be 3-20 lowercase letters, digits or hyphens and start with a letter."
  }
}

variable "generated_secrets" {
  description = <<-EOT
    Secrets whose value Terraform generates and no human ever sees, keyed by short
    name, e.g. { "redis-auth-token" = { description = "...", length = 64 } }.
    Values come from an ephemeral random_password and are written with
    write-only arguments, so they appear in neither plans nor state. Bump
    `version` to rotate.
  EOT
  type = map(object({
    description = string
    length      = optional(number, 48)
    special     = optional(bool, false)
    version     = optional(number, 1)
  }))
  default = {}

  validation {
    condition     = alltrue([for s in values(var.generated_secrets) : s.length >= 32 && s.length <= 128])
    error_message = "generated secrets must be 32-128 characters long."
  }
}

variable "external_secrets" {
  description = "Secrets whose value is supplied out-of-band after apply (e.g. `aws secretsmanager put-secret-value`), such as third-party API keys. Terraform creates the encrypted container and access policy only; the value never passes through Terraform."
  type = map(object({
    description = string
  }))
  default = {}
}

variable "recovery_window_in_days" {
  description = "Days a deleted secret remains recoverable (0 deletes immediately; use 0 only for throwaway environments)."
  type        = number
  default     = 30

  validation {
    condition     = var.recovery_window_in_days == 0 || (var.recovery_window_in_days >= 7 && var.recovery_window_in_days <= 30)
    error_message = "recovery_window_in_days must be 0 or between 7 and 30."
  }
}

variable "kms_key_deletion_window_in_days" {
  description = "Waiting period before the customer-managed KMS key is deleted."
  type        = number
  default     = 30

  validation {
    condition     = var.kms_key_deletion_window_in_days >= 7 && var.kms_key_deletion_window_in_days <= 30
    error_message = "kms_key_deletion_window_in_days must be between 7 and 30."
  }
}

variable "tags" {
  description = "Extra tags for this module's resources (provider default_tags already apply)."
  type        = map(string)
  default     = {}
}
