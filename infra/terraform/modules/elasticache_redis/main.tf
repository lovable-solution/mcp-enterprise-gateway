# -----------------------------------------------------------------------------
# Module: elasticache_redis  (Phase 1: interface only; resources land in Phase 4)
#
# Holds the gateway's enforcement state: per-session budget ledgers, loop
# detection windows, circuit-breaker state, approval pub/sub.
#
# Planned resources:
#   aws_elasticache_subnet_group (private-data subnets),
#   aws_security_group + one ingress rule (6379) per allowed_security_group_ids,
#   aws_elasticache_parameter_group with maxmemory-policy = noeviction
#     (ElastiCache defaults to volatile-lru, which could evict a ledger or breaker
#     key and silently reset a budget; with noeviction writes fail and the gateway
#     fails closed),
#   aws_elasticache_replication_group: transit_encryption_enabled (TLS required),
#     at_rest_encryption_enabled, auth token via write-only argument, Multi-AZ +
#     automatic failover when num_cache_clusters >= 2, slow-log delivery to
#     CloudWatch Logs, auto minor upgrades.
# -----------------------------------------------------------------------------

locals {
  replication_group_id = "${var.name}-redis"
}
