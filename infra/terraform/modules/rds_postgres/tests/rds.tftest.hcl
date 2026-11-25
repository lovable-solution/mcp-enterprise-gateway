# PostgreSQL hardening (mocked AWS provider: no account needed).

mock_provider "aws" {
  source = "../../tests/mocks/aws"
}

variables {
  name        = "mcpgw-test"
  vpc_id      = "vpc-0123456789abcdef0"
  subnet_ids  = ["subnet-0aaaaaaaaaaaaaaaa", "subnet-0bbbbbbbbbbbbbbbb"]
  kms_key_arn = "arn:aws:kms:us-east-1:111122223333:key/00000000-0000-0000-0000-000000000000"
  allowed_security_group_ids = {
    gateway = "sg-0aaaaaaaaaaaaaaaa"
  }
}

run "credentials_belong_to_rds" {
  command = apply

  assert {
    condition     = aws_db_instance.this.manage_master_user_password && aws_db_instance.this.password == null
    error_message = "the master password must be generated and rotated by RDS, never set by Terraform"
  }

  assert {
    condition     = output.master_user_secret_arn == "arn:aws:secretsmanager:us-east-1:111122223333:secret:rds!db-mock-AbCdEf"
    error_message = "the master secret's ARN must be exported for the bootstrap task"
  }
}

run "private_encrypted_tls_only" {
  command = apply

  assert {
    condition     = !aws_db_instance.this.publicly_accessible && aws_db_instance.this.storage_encrypted
    error_message = "the database must be private and encrypted at rest"
  }

  assert {
    condition     = anytrue([for p in aws_db_parameter_group.this.parameter : p.name == "rds.force_ssl" && p.value == "1"])
    error_message = "unencrypted connections must be refused"
  }

  assert {
    condition     = anytrue([for p in aws_db_parameter_group.this.parameter : p.name == "password_encryption" && p.value == "scram-sha-256"])
    error_message = "passwords must be hashed with SCRAM"
  }

  assert {
    condition     = aws_db_instance.this.deletion_protection && !aws_db_instance.this.skip_final_snapshot
    error_message = "deletion protection and a final snapshot are on by default"
  }
}

run "only_listed_clients_reach_the_port" {
  command = apply

  assert {
    condition     = keys(aws_vpc_security_group_ingress_rule.clients) == ["gateway"]
    error_message = "ingress must be granted per client security group"
  }

  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.clients["gateway"].referenced_security_group_id == "sg-0aaaaaaaaaaaaaaaa" &&
      aws_vpc_security_group_ingress_rule.clients["gateway"].from_port == 5432 &&
      aws_vpc_security_group_ingress_rule.clients["gateway"].cidr_ipv4 == null
    )
    error_message = "clients are security groups on 5432, never CIDR ranges"
  }
}

run "storage_ceiling_below_floor_is_refused" {
  command = plan

  variables {
    allocated_storage_gb     = 50
    max_allocated_storage_gb = 20
  }

  expect_failures = [var.max_allocated_storage_gb]
}
