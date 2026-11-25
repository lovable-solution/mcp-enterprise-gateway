# -----------------------------------------------------------------------------
# Module: secrets_manager  (Phase 1: interface only; resources land in Phase 4)
#
# Zero hardcoded secrets: nothing in code, tfvars, plans or state.
#   * generated secrets: ephemeral "random_password" -> secret_string_wo
#     (write-only; rotation by bumping `version`)
#   * external secrets:  container + policy only; the value is set out-of-band
#   * RDS master credentials are NOT here: RDS manages them itself
#     (manage_master_user_password), see the rds_postgres module.
#
# Planned resources:
#   aws_kms_key (rotation enabled) + aws_kms_alias "alias/<name>-secrets",
#   aws_secretsmanager_secret for each generated and external secret,
#   ephemeral "random_password" + aws_secretsmanager_secret_version (write-only)
#     for each generated secret,
#   data "aws_iam_policy_document" "read": secretsmanager:GetSecretValue on these
#     ARNs + kms:Decrypt on the key (attached to ECS task *execution* roles only).
# -----------------------------------------------------------------------------

locals {
  secret_names = merge(
    { for key in keys(var.generated_secrets) : key => "${var.name}/${key}" },
    { for key in keys(var.external_secrets) : key => "${var.name}/${key}" },
  )
}
