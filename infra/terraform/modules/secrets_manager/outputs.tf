output "secret_names" {
  description = "Secret name by key (\"<name>/<key>\")."
  value       = local.secret_names
}

output "secret_arns" {
  description = <<-EOT
    Secret ARN by key, for ECS task-definition `secrets` (valueFrom). A generated
    secret's ARN is taken from its value resource, so anything that references it
    is ordered after the value has been written (an ephemeral read-back sees the
    current value, and tasks never start against an empty secret).
  EOT
  value = merge(
    { for key, version in aws_secretsmanager_secret_version.generated : key => version.secret_arn },
    { for key in keys(var.external_secrets) : key => aws_secretsmanager_secret.this[key].arn },
  )
}

output "secret_versions" {
  description = "Current `version` of each generated secret. Consumers holding a copy of the value (ElastiCache AUTH) pass it as their write-only version, so one bump rotates both."
  value       = { for key, secret in var.generated_secrets : key => secret.version }
}

output "kms_key_arn" {
  description = "Customer-managed KMS key encrypting every secret in this module."
  value       = aws_kms_key.secrets.arn
}

output "read_policy_json" {
  description = "IAM policy (JSON) allowing GetSecretValue on these secrets and kms:Decrypt on the key."
  value       = data.aws_iam_policy_document.read.json
}
