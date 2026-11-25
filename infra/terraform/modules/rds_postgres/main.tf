# -----------------------------------------------------------------------------
# Module: rds_postgres  (Phase 1: interface only; resources land in Phase 4)
#
# PostgreSQL for the gateway's approval store (and, when the agent runtime is
# deployed, a separate database + role for LangGraph checkpoints, mirroring the
# local compose setup).
#
# Planned resources:
#   aws_db_subnet_group (private-data subnets),
#   aws_security_group + one ingress rule (5432) per allowed_security_group_ids,
#   aws_db_parameter_group: rds.force_ssl=1, log_min_duration_statement,
#     password_encryption=scram-sha-256,
#   aws_db_instance: gp3, storage_encrypted, manage_master_user_password=true
#     (master secret owned and rotated by RDS: never in Terraform), IAM DB auth,
#     Database Insights + enhanced monitoring, auto minor upgrades,
#     copy_tags_to_snapshot, final snapshot, deletion_protection.
# Service roles (gateway, agent runtime) are created by a one-off migration task
# that reads the master secret, not by Terraform (no DB provider in the pipeline).
# -----------------------------------------------------------------------------

locals {
  identifier = "${var.name}-postgres"
}
