# -----------------------------------------------------------------------------
# Module: secrets_manager
#
# Zero hardcoded secrets: nothing in code, tfvars, plans or state.
#   * generated secrets: an ephemeral random_password, written with the write-only
#     secret_string_wo. Terraform never stores the value; bumping `version` writes
#     a fresh one (rotation). Consumers that must hold the same value (ElastiCache's
#     AUTH token) read it back ephemerally.
#   * external secrets: the encrypted container only; the value is set out-of-band
#     (`aws secretsmanager put-secret-value`) and never passes through Terraform.
#   * RDS master credentials are NOT here: RDS generates, stores and rotates them
#     itself (manage_master_user_password), see the rds_postgres module.
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  secret_names = merge(
    { for key in keys(var.generated_secrets) : key => "${var.name}/${key}" },
    { for key in keys(var.external_secrets) : key => "${var.name}/${key}" },
  )

  descriptions = merge(
    { for key, secret in var.generated_secrets : key => secret.description },
    { for key, secret in var.external_secrets : key => secret.description },
  )
}

# Key administration stays with the account (IAM policies decide who may use the key);
# services such as RDS and ElastiCache obtain grants through the calling principal.
data "aws_iam_policy_document" "key" {
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  # CloudWatch Logs encrypts this account's log groups with the key (container logs,
  # VPC flow logs, the cache's slow log), so logs share the secrets' key and rotation.
  statement {
    sid       = "CloudWatchLogsOfThisAccount"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${data.aws_partition.current.partition}:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:*"]
    }
  }
}

resource "aws_kms_key" "secrets" {
  description             = "${var.name}: secrets, data and logs at rest"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_key_deletion_window_in_days
  policy                  = data.aws_iam_policy_document.key.json

  tags = var.tags
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${var.name}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

resource "aws_secretsmanager_secret" "this" {
  for_each = local.secret_names

  name                    = each.value
  description             = local.descriptions[each.key]
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = var.recovery_window_in_days

  tags = var.tags
}

# A fresh value on every run, but only *written* when `version` changes.
ephemeral "random_password" "generated" {
  for_each = var.generated_secrets

  length  = each.value.length
  special = each.value.special
  # Characters that survive URLs, DSNs and shell quoting unescaped.
  override_special = "-_.~"
}

resource "aws_secretsmanager_secret_version" "generated" {
  for_each = var.generated_secrets

  secret_id                = aws_secretsmanager_secret.this[each.key].id
  secret_string_wo         = ephemeral.random_password.generated[each.key].result
  secret_string_wo_version = each.value.version
}

data "aws_iam_policy_document" "read" {
  statement {
    sid       = "ReadSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for secret in aws_secretsmanager_secret.this : secret.arn]
  }

  statement {
    sid       = "DecryptSecrets"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.secrets.arn]
  }
}
