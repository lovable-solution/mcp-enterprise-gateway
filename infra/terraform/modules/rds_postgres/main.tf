# -----------------------------------------------------------------------------
# Module: rds_postgres
#
# PostgreSQL for the gateway's approval store and the agent runtime's LangGraph
# checkpoints, in separate databases owned by separate roles (as in compose).
#
# The master credential is generated, stored and rotated by RDS itself
# (manage_master_user_password): it never exists in Terraform. Service roles and
# their databases are created by the one-off `db-bootstrap` ECS task, the only
# workload allowed to read the master secret (no database provider in the
# Terraform pipeline, so no database credentials in it either).
# -----------------------------------------------------------------------------

locals {
  identifier = "${var.name}-postgres"
  family     = "postgres${split(".", var.engine_version)[0]}"
}

resource "aws_db_subnet_group" "this" {
  name        = local.identifier
  description = "${local.identifier}: private-data subnets (no internet route)"
  subnet_ids  = var.subnet_ids

  tags = var.tags
}

resource "aws_security_group" "this" {
  name        = local.identifier
  description = "PostgreSQL ${local.identifier}: clients listed in allowed_security_group_ids only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = local.identifier })
}

# No egress rules: the database never initiates connections.
resource "aws_vpc_security_group_ingress_rule" "clients" {
  for_each = var.allowed_security_group_ids

  security_group_id            = aws_security_group.this.id
  description                  = "PostgreSQL from ${each.key}"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_parameter_group" "this" {
  name        = "${local.identifier}-${local.family}"
  family      = local.family
  description = "${local.identifier}: TLS only, SCRAM passwords, slow-query logging"

  parameter {
    name  = "rds.force_ssl"
    value = "1" # reject unencrypted connections
  }

  parameter {
    name  = "password_encryption"
    value = "scram-sha-256"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000" # log statements slower than 1 s
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

data "aws_iam_policy_document" "monitoring_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "monitoring" {
  name               = "${local.identifier}-monitoring"
  assume_role_policy = data.aws_iam_policy_document.monitoring_assume.json

  tags = var.tags
}

data "aws_partition" "current" {}

resource "aws_iam_role_policy_attachment" "monitoring" {
  role       = aws_iam_role.monitoring.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_db_instance" "this" {
  identifier     = local.identifier
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class
  port           = 5432

  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = var.kms_key_arn

  db_name  = var.database_name
  username = var.master_username
  # RDS generates the password, keeps it in Secrets Manager and rotates it.
  manage_master_user_password   = true
  master_user_secret_kms_key_id = var.kms_key_arn

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  publicly_accessible    = false
  parameter_group_name   = aws_db_parameter_group.this.name
  ca_cert_identifier     = "rds-ca-rsa2048-g1"

  iam_database_authentication_enabled = true
  multi_az                            = var.multi_az

  backup_retention_period  = var.backup_retention_days
  backup_window            = "03:00-03:30"
  maintenance_window       = "sun:04:00-sun:05:00"
  copy_tags_to_snapshot    = true
  delete_automated_backups = false

  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false
  apply_immediately           = false

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.kms_key_arn
  performance_insights_retention_period = 7
  database_insights_mode                = "standard"
  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.monitoring.arn
  enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.identifier}-final"

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.monitoring]
}
