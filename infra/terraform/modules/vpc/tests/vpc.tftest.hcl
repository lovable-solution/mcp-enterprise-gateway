# Network tiers and isolation (mocked AWS provider: no account needed).

mock_provider "aws" {
  source = "../../tests/mocks/aws"
}

variables {
  name = "mcpgw-test"
}

run "three_tiers_in_every_az" {
  command = apply

  assert {
    condition     = length(aws_subnet.public) == 2 && length(aws_subnet.private_app) == 2 && length(aws_subnet.private_data) == 2
    error_message = "each tier needs one subnet per AZ"
  }

  assert {
    condition     = output.availability_zones == tolist(["us-east-1a", "us-east-1b"])
    error_message = "subnets must span the first az_count AZs"
  }

  assert {
    condition = (
      aws_subnet.public["us-east-1a"].cidr_block == "10.40.0.0/22" &&
      aws_subnet.private_data["us-east-1a"].cidr_block == "10.40.16.0/22" &&
      aws_subnet.private_app["us-east-1a"].cidr_block == "10.40.64.0/20"
    )
    error_message = "the address plan changed (public /20, data /20, app /18 per tier)"
  }

  assert {
    condition     = alltrue([for subnet in aws_subnet.public : !subnet.map_public_ip_on_launch])
    error_message = "nothing may receive a public IP automatically"
  }
}

run "the_data_tier_has_no_route_out" {
  command = apply

  assert {
    condition     = length(aws_route_table.private_data.route) == 0
    error_message = "the data tier must have local routes only"
  }

  assert {
    condition     = alltrue([for table in aws_route_table.private_app : length(table.route) == 1 && one(table.route).cidr_block == "0.0.0.0/0" && one(table.route).nat_gateway_id != ""])
    error_message = "the app tier must leave through NAT only"
  }

  assert {
    condition     = length(aws_nat_gateway.this) == 1
    error_message = "single NAT mode creates one gateway"
  }
}

run "aws_apis_through_endpoints_and_flow_logs_on" {
  command = apply

  assert {
    condition     = aws_vpc_endpoint.s3.vpc_endpoint_type == "Gateway" && length(aws_vpc_endpoint.interface) == 6
    error_message = "ECR, Secrets Manager, Logs, STS and KMS must be reachable without the NAT gateway"
  }

  assert {
    condition     = alltrue([for endpoint in aws_vpc_endpoint.interface : endpoint.private_dns_enabled])
    error_message = "interface endpoints need private DNS"
  }

  assert {
    condition     = aws_flow_log.this[0].traffic_type == "ALL"
    error_message = "flow logs must capture all traffic"
  }

  assert {
    condition     = length(aws_default_security_group.this.ingress) == 0 && length(aws_default_security_group.this.egress) == 0
    error_message = "the default security group must have no rules"
  }
}

run "per_az_nat_and_no_flow_logs" {
  command = apply

  variables {
    az_count                 = 3
    nat_gateway_mode         = "per_az"
    flow_logs_retention_days = 0
  }

  assert {
    condition     = length(aws_nat_gateway.this) == 3 && length(aws_flow_log.this) == 0
    error_message = "per_az mode needs one NAT gateway per AZ; retention 0 disables flow logs"
  }
}

run "small_vpcs_are_refused" {
  command = plan

  variables {
    cidr_block = "10.40.0.0/24"
  }

  expect_failures = [var.cidr_block]
}
