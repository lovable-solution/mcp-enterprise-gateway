# Plan-time tests with a mocked AWS provider: no account, no credentials, no cost.

mock_provider "aws" {
  source = "../../tests/mocks/aws"
}

mock_provider "random" {}

variables {
  name = "mcpgw-test"
  generated_secrets = {
    "db-password" = { description = "generated" }
    "redis-token" = { description = "generated", length = 64, version = 3 }
  }
  external_secrets = {
    "api-key" = { description = "set out-of-band" }
  }
}

run "generated_values_never_reach_state" {
  command = apply

  assert {
    condition     = alltrue([for version in aws_secretsmanager_secret_version.generated : version.secret_string_wo == null && version.secret_string == null])
    error_message = "a generated secret's value was stored in state"
  }

  assert {
    condition     = aws_secretsmanager_secret_version.generated["redis-token"].secret_string_wo_version == 3
    error_message = "rotation must be driven by the secret's version"
  }

  assert {
    condition     = output.secret_versions == { "db-password" = 1, "redis-token" = 3 }
    error_message = "secret_versions must expose each generated secret's version"
  }
}

run "external_secrets_are_containers_only" {
  command = apply

  assert {
    condition     = length(aws_secretsmanager_secret.this) == 3 && length(aws_secretsmanager_secret_version.generated) == 2
    error_message = "external secrets must not get a value from Terraform"
  }

  assert {
    condition     = output.secret_names["api-key"] == "mcpgw-test/api-key"
    error_message = "secrets are named <name>/<key>"
  }
}

run "everything_is_encrypted_with_a_rotating_customer_key" {
  command = apply

  assert {
    condition     = aws_kms_key.secrets.enable_key_rotation
    error_message = "the customer-managed key must rotate"
  }

  assert {
    condition     = alltrue([for secret in aws_secretsmanager_secret.this : secret.kms_key_id == aws_kms_key.secrets.arn])
    error_message = "every secret must use the module's customer-managed key"
  }

  assert {
    condition     = alltrue([for secret in aws_secretsmanager_secret.this : secret.recovery_window_in_days == 30])
    error_message = "deleted secrets stay recoverable for 30 days by default"
  }
}

run "short_generated_secrets_are_refused" {
  command = plan

  variables {
    generated_secrets = { weak = { description = "too short", length = 16 } }
  }

  expect_failures = [var.generated_secrets]
}
