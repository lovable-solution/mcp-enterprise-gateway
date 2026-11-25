# Output contract. Values are null placeholders until Phase 4 implements the
# resources; names and meanings are fixed so callers can be wired now.

output "replication_group_id" {
  description = "ElastiCache replication group id."
  value       = local.replication_group_id
}

output "primary_endpoint_address" {
  description = "Primary endpoint (reads and writes; TLS)."
  value       = null # Phase 4: aws_elasticache_replication_group.this.primary_endpoint_address
}

output "reader_endpoint_address" {
  description = "Reader endpoint (replicas)."
  value       = null # Phase 4: aws_elasticache_replication_group.this.reader_endpoint_address
}

output "port" {
  description = "Redis port."
  value       = 6379
}

output "security_group_id" {
  description = "Security group attached to the cache nodes."
  value       = null # Phase 4: aws_security_group.this.id
}
