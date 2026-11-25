# Output contract. Values are null placeholders until Phase 4 implements the
# resources; names and meanings are fixed so callers can be wired now.

output "secret_names" {
  description = "Secret name by key (\"<name>/<key>\")."
  value       = local.secret_names
}

output "secret_arns" {
  description = "Secret ARN by key, for ECS task-definition `secrets` (valueFrom)."
  value       = null # Phase 4: { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "kms_key_arn" {
  description = "Customer-managed KMS key encrypting every secret in this module."
  value       = null # Phase 4: aws_kms_key.secrets.arn
}

output "read_policy_json" {
  description = "IAM policy (JSON) allowing GetSecretValue on these secrets and kms:Decrypt on the key."
  value       = null # Phase 4: data.aws_iam_policy_document.read.json
}
