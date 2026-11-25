# Output contract. Values are null placeholders until Phase 4 implements the
# resources; names and meanings are fixed so callers can be wired now.

output "identifier" {
  description = "DB instance identifier."
  value       = local.identifier
}

output "address" {
  description = "DNS name of the instance endpoint (TLS required)."
  value       = null # Phase 4: aws_db_instance.this.address
}

output "port" {
  description = "Postgres port."
  value       = 5432
}

output "database_name" {
  description = "Initial database name."
  value       = var.database_name
}

output "master_user_secret_arn" {
  description = "Secrets Manager ARN of the RDS-managed master credential (migration task only)."
  value       = null # Phase 4: aws_db_instance.this.master_user_secret[0].secret_arn
}

output "security_group_id" {
  description = "Security group attached to the instance."
  value       = null # Phase 4: aws_security_group.this.id
}
