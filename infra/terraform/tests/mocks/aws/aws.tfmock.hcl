# Shared mock values for plan/apply tests (`terraform test` with mock_provider):
# realistic shapes wherever the AWS provider validates a value (JSON policies, ARNs).

mock_data "aws_caller_identity" {
  defaults = { account_id = "111122223333" }
}

mock_data "aws_partition" {
  defaults = { partition = "aws", dns_suffix = "amazonaws.com" }
}

mock_data "aws_region" {
  defaults = { region = "us-east-1", name = "us-east-1" }
}

mock_data "aws_availability_zones" {
  defaults = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }
}

mock_data "aws_iam_policy_document" {
  defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

mock_resource "aws_kms_key" {
  defaults = { arn = "arn:aws:kms:us-east-1:111122223333:key/00000000-0000-0000-0000-000000000000" }
}

mock_resource "aws_secretsmanager_secret" {
  defaults = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:mcpgw/mock-AbCdEf" }
}

mock_resource "aws_secretsmanager_secret_version" {
  defaults = { secret_arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:mcpgw/mock-AbCdEf" }
}

mock_resource "aws_iam_role" {
  defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
}

mock_resource "aws_cloudwatch_log_group" {
  defaults = { arn = "arn:aws:logs:us-east-1:111122223333:log-group:mock" }
}

mock_resource "aws_db_instance" {
  defaults = {
    address = "mcpgw-test-postgres.abcdefghijkl.us-east-1.rds.amazonaws.com"
    port    = 5432
    master_user_secret = [{
      secret_arn    = "arn:aws:secretsmanager:us-east-1:111122223333:secret:rds!db-mock-AbCdEf"
      kms_key_id    = "arn:aws:kms:us-east-1:111122223333:key/00000000-0000-0000-0000-000000000000"
      secret_status = "active"
    }]
  }
}

mock_resource "aws_elasticache_replication_group" {
  defaults = {
    primary_endpoint_address = "master.mcpgw-test-redis.abcdef.use1.cache.amazonaws.com"
    reader_endpoint_address  = "replica.mcpgw-test-redis.abcdef.use1.cache.amazonaws.com"
    port                     = 6379
  }
}

mock_resource "aws_service_discovery_http_namespace" {
  defaults = { arn = "arn:aws:servicediscovery:us-east-1:111122223333:namespace/ns-mock" }
}

mock_resource "aws_ecs_cluster" {
  defaults = { arn = "arn:aws:ecs:us-east-1:111122223333:cluster/mock" }
}

mock_resource "aws_ecs_task_definition" {
  defaults = { arn = "arn:aws:ecs:us-east-1:111122223333:task-definition/mock:1" }
}

mock_resource "aws_lb" {
  defaults = {
    arn      = "arn:aws:elasticloadbalancing:us-east-1:111122223333:loadbalancer/app/mock/0123456789abcdef"
    dns_name = "internal-mock-123456789.us-east-1.elb.amazonaws.com"
    zone_id  = "Z35SXDOTRQ7X7K"
  }
}

mock_resource "aws_lb_listener" {
  defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:111122223333:listener/app/mock/0123456789abcdef/0123456789abcdef" }
}

mock_resource "aws_lb_target_group" {
  defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:111122223333:targetgroup/mock/0123456789abcdef" }
}

# Ephemeral read-back of a generated secret (e.g. the ElastiCache AUTH token, which
# the provider validates: 16-128 characters, no @, " or /).
mock_ephemeral "aws_secretsmanager_secret_version" {
  defaults = { secret_string = "mock-token-0123456789abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH" }
}
