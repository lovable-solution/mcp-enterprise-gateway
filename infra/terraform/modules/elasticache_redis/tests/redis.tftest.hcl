# ElastiCache hardening (mocked AWS provider: no account needed).

mock_provider "aws" {
  source = "../../tests/mocks/aws"
}

variables {
  name                  = "mcpgw-test"
  vpc_id                = "vpc-0123456789abcdef0"
  subnet_ids            = ["subnet-0aaaaaaaaaaaaaaaa", "subnet-0bbbbbbbbbbbbbbbb"]
  auth_token_secret_arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:mcpgw-test/redis-auth-token-AbCdEf"
  auth_token_version    = 3
  allowed_security_group_ids = {
    gateway = "sg-0aaaaaaaaaaaaaaaa"
  }
}

run "budgets_are_never_evicted" {
  command = apply

  assert {
    condition     = anytrue([for p in aws_elasticache_parameter_group.this.parameter : p.name == "maxmemory-policy" && p.value == "noeviction"])
    error_message = "an evicted ledger or breaker key would silently reset a budget"
  }

  assert {
    condition     = aws_elasticache_parameter_group.this.family == "redis7"
    error_message = "the parameter group family follows the engine's major version"
  }
}

run "tls_encryption_and_write_only_auth" {
  command = apply

  assert {
    condition     = aws_elasticache_replication_group.this.transit_encryption_enabled && aws_elasticache_replication_group.this.transit_encryption_mode == "required"
    error_message = "clients must use TLS"
  }

  assert {
    condition     = aws_elasticache_replication_group.this.at_rest_encryption_enabled
    error_message = "snapshots and nodes must be encrypted at rest"
  }

  assert {
    condition = (
      aws_elasticache_replication_group.this.auth_token == null &&
      aws_elasticache_replication_group.this.auth_token_wo == null &&
      aws_elasticache_replication_group.this.auth_token_wo_version == 3
    )
    error_message = "the AUTH token must be write-only (never in state), rotated by version"
  }
}

run "two_nodes_fail_over_across_azs" {
  command = apply

  assert {
    condition     = aws_elasticache_replication_group.this.automatic_failover_enabled && aws_elasticache_replication_group.this.multi_az_enabled
    error_message = "a primary and a replica must fail over automatically"
  }
}

run "a_single_node_cannot_fail_over" {
  command = apply

  variables {
    num_cache_clusters = 1
  }

  assert {
    condition     = !aws_elasticache_replication_group.this.automatic_failover_enabled && !aws_elasticache_replication_group.this.multi_az_enabled
    error_message = "failover needs a replica"
  }
}

run "valkey_needs_a_valkey_version" {
  command = plan

  variables {
    engine         = "valkey"
    engine_version = "7.1"
  }

  expect_failures = [var.engine_version]
}
