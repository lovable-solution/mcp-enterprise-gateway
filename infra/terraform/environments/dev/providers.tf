provider "aws" {
  region = var.aws_region

  # Refuse to run against any other account, whatever credentials are active.
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = local.tags
  }
}
