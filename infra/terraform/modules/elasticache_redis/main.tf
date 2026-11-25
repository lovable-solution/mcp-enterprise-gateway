# -----------------------------------------------------------------------------
# Module: elasticache_redis
#
# Holds the gateway's enforcement state: per-session budget ledgers, loop
# detection windows, circuit-breaker state and the event streams.
#
#   * maxmemory-policy = noeviction: ElastiCache defaults to volatile-lru, which
#     could evict a ledger or breaker key and silently reset a budget. With
#     noeviction writes fail instead, and the gateway fails closed.
#   * TLS required, encrypted at rest, AUTH token from Secrets Manager: read
#     ephemerally and passed write-only, so it never enters plans or state.
# -----------------------------------------------------------------------------

locals {
  replication_group_id = "${var.name}-redis"
  family               = "${var.engine}${split(".", var.engine_version)[0]}"
  failover             = var.num_cache_clusters >= 2
}

resource "aws_elasticache_subnet_group" "this" {
  name        = local.replication_group_id
  description = "${local.replication_group_id}: private-data subnets (no internet route)"
  subnet_ids  = var.subnet_ids

  tags = var.tags
}

resource "aws_security_group" "this" {
  name        = local.replication_group_id
  description = "ElastiCache ${local.replication_group_id}: clients listed in allowed_security_group_ids only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = local.replication_group_id })
}

resource "aws_vpc_security_group_ingress_rule" "clients" {
  for_each = var.allowed_security_group_ids

  security_group_id            = aws_security_group.this.id
  description                  = "Redis (TLS) from ${each.key}"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}

resource "aws_elasticache_parameter_group" "this" {
  name        = "${local.replication_group_id}-${local.family}"
  family      = local.family
  description = "${local.replication_group_id}: noeviction (budgets and breakers must never be evicted)"

  parameter {
    name  = "maxmemory-policy"
    value = "noeviction"
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "slow_log" {
  name              = "/elasticache/${local.replication_group_id}/slow-log"
  retention_in_days = 30
  kms_key_id        = var.logs_kms_key_arn

  tags = var.tags
}

# Read back the token the secrets module generated: the value exists only in memory
# for this run and reaches ElastiCache through a write-only argument.
ephemeral "aws_secretsmanager_secret_version" "auth_token" {
  secret_id = var.auth_token_secret_arn
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = local.replication_group_id
  description          = "MCP gateway enforcement state (budgets, loop windows, breakers, events)"

  engine               = var.engine
  engine_version       = var.engine_version
  node_type            = var.node_type
  num_cache_clusters   = var.num_cache_clusters
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.this.name
  subnet_group_name    = aws_elasticache_subnet_group.this.name
  security_group_ids   = [aws_security_group.this.id]

  automatic_failover_enabled = local.failover
  multi_az_enabled           = local.failover

  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  at_rest_encryption_enabled = true
  kms_key_id                 = var.kms_key_arn

  auth_token_wo              = ephemeral.aws_secretsmanager_secret_version.auth_token.secret_string
  auth_token_wo_version      = var.auth_token_version
  auth_token_update_strategy = "ROTATE" # old and new tokens both work until clients restart

  snapshot_retention_limit   = var.snapshot_retention_days
  snapshot_window            = "02:00-03:00"
  maintenance_window         = "sun:05:00-sun:06:00"
  auto_minor_version_upgrade = true
  apply_immediately          = false

  log_delivery_configuration {
    destination      = aws_cloudwatch_log_group.slow_log.name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "slow-log"
  }

  tags = var.tags
}
