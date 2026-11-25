# Trust topology of the ECS deployment (mocked AWS provider: no account needed).

mock_provider "aws" {
  source = "../../tests/mocks/aws"
}

variables {
  name                = "mcpgw-test"
  vpc_id              = "vpc-0123456789abcdef0"
  alb_subnet_ids      = ["subnet-0aaaaaaaaaaaaaaaa", "subnet-0bbbbbbbbbbbbbbbb"]
  service_subnet_ids  = ["subnet-0aaaaaaaaaaaaaaaa", "subnet-0bbbbbbbbbbbbbbbb"]
  alb_ingress_cidrs   = ["10.0.0.0/8"]
  certificate_arn     = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  secrets_kms_key_arn = "arn:aws:kms:us-east-1:111122223333:key/00000000-0000-0000-0000-000000000000"
  otel_sidecar_image  = "public.ecr.aws/aws-observability/aws-otel-collector@sha256:1111111111111111111111111111111111111111111111111111111111111111"

  services = {
    gateway = {
      image                 = "111122223333.dkr.ecr.us-east-1.amazonaws.com/mcp-gateway@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      container_port        = 8000
      max_count             = 4
      expose_via_alb        = true
      alb_path_patterns     = ["/mcp", "/mcp/*", "/v1/*", "/healthz", "/readyz", "/extra"]
      alb_priority          = 100
      ingress_from_services = ["operator-ui"]
      egress_to = {
        postgres = { security_group_id = "sg-0ddddddddddddddd1", port = 5432 }
      }
      environment = { GATEWAY_ENVIRONMENT = "test" }
      secrets = {
        GATEWAY_DATABASE_PASSWORD = "arn:aws:secretsmanager:us-east-1:111122223333:secret:mcpgw-test/gateway-db-password-AbCdEf"
      }
    }
    crm-ops = {
      image                 = "111122223333.dkr.ecr.us-east-1.amazonaws.com/crm-ops-mcp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      container_port        = 8001
      ingress_from_services = ["gateway"]
      secrets = {
        CRM_OPS_GATEWAY_ASSERTION_KEY = "arn:aws:secretsmanager:us-east-1:111122223333:secret:mcpgw-test/upstream-assertion-key-AbCdEf"
      }
    }
    operator-ui = {
      image             = "111122223333.dkr.ecr.us-east-1.amazonaws.com/operator-ui@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      container_port    = 3000
      expose_via_alb    = true
      alb_path_patterns = ["/*"]
      alb_priority      = 1000
      writable_paths    = ["/tmp", "/app/.next/cache"]
      otel_sidecar      = false
    }
  }

  tasks = {
    db-bootstrap = {
      image   = "111122223333.dkr.ecr.us-east-1.amazonaws.com/postgres@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      user    = "70:70"
      command = ["sh", "-c", "psql --version"]
      egress_to = {
        postgres = { security_group_id = "sg-0ddddddddddddddd1", port = 5432 }
      }
      secrets = {
        PGPASSWORD = "arn:aws:secretsmanager:us-east-1:111122223333:secret:rds!db-mock-AbCdEf:password::"
      }
    }
    agent-runtime = {
      image        = "111122223333.dkr.ecr.us-east-1.amazonaws.com/agent-runtime@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      alb_access   = true
      otel_sidecar = true
    }
  }
}

run "only_the_gateway_and_console_face_clients" {
  command = apply

  assert {
    condition     = output.exposed_services == tolist(["gateway", "operator-ui"]) && keys(aws_lb_target_group.this) == ["gateway", "operator-ui"]
    error_message = "only the gateway and the console may sit behind the load balancer"
  }

  assert {
    condition     = aws_lb.this.internal && aws_lb.this.drop_invalid_header_fields
    error_message = "the load balancer must be internal and drop malformed headers"
  }

  assert {
    condition     = !contains(keys(aws_vpc_security_group_ingress_rule.service_from_alb), "crm-ops")
    error_message = "an MCP server must never accept traffic from the load balancer"
  }
}

run "mcp_servers_admit_the_gateway_only" {
  command = apply

  assert {
    condition     = [for key, rule in aws_vpc_security_group_ingress_rule.east_west : key if endswith(key, "->crm-ops")] == ["gateway->crm-ops"]
    error_message = "crm-ops must admit exactly one caller"
  }

  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.east_west["gateway->crm-ops"].referenced_security_group_id == aws_security_group.workload["gateway"].id &&
      aws_vpc_security_group_ingress_rule.east_west["gateway->crm-ops"].from_port == 8001 &&
      aws_vpc_security_group_ingress_rule.east_west["gateway->crm-ops"].cidr_ipv4 == null
    )
    error_message = "crm-ops admits the gateway's security group, on its port, and no address range"
  }
}

run "tasks_accept_no_traffic" {
  command = apply

  assert {
    condition = alltrue([
      for rule in concat(values(aws_vpc_security_group_ingress_rule.east_west), values(aws_vpc_security_group_ingress_rule.service_from_alb)) :
      !contains([aws_security_group.workload["db-bootstrap"].id, aws_security_group.workload["agent-runtime"].id], rule.security_group_id)
    ])
    error_message = "on-demand tasks must not accept inbound traffic"
  }

  assert {
    condition     = keys(aws_vpc_security_group_ingress_rule.alb_from_tasks) == ["agent-runtime:443"]
    error_message = "only the agent task may reach the load balancer (like any other agent)"
  }
}

run "tls_and_path_routing" {
  command = apply

  assert {
    condition     = aws_lb_listener.https[0].ssl_policy == "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"
    error_message = "HTTPS must use the TLS 1.3/1.2 forward-secrecy policy"
  }

  assert {
    condition     = length(aws_lb_listener.http) == 0 && length(aws_vpc_security_group_ingress_rule.alb_from_cidrs) == 1
    error_message = "with a certificate there is no plaintext listener, and only 443 is open"
  }

  assert {
    condition = (
      output.alb_routes["gateway-0"].priority == 100 && length(output.alb_routes["gateway-0"].paths) == 5 &&
      output.alb_routes["gateway-1"].priority == 101 && output.alb_routes["gateway-1"].paths == tolist(["/extra"]) &&
      output.alb_routes["operator-ui-0"].priority == 1000 && output.alb_routes["operator-ui-0"].paths == tolist(["/*"])
    )
    error_message = "the gateway's paths (5 per rule) must be evaluated before the console's catch-all"
  }
}

run "workloads_are_hardened" {
  command = apply

  assert {
    condition     = alltrue([for service in aws_ecs_service.this : !service.network_configuration[0].assign_public_ip && !service.enable_execute_command])
    error_message = "tasks get no public IP and no interactive shell"
  }

  assert {
    condition     = alltrue([for service in aws_ecs_service.this : service.deployment_circuit_breaker[0].enable && service.deployment_circuit_breaker[0].rollback])
    error_message = "failed deployments must roll back"
  }

  assert {
    condition     = alltrue([for definition in aws_ecs_task_definition.this : alltrue([for container in jsondecode(definition.container_definitions) : container.readonlyRootFilesystem])])
    error_message = "every container runs on a read-only root filesystem"
  }

  assert {
    condition = (
      !contains([for env in jsondecode(aws_ecs_task_definition.this["gateway"].container_definitions)[0].environment : env.name], "GATEWAY_DATABASE_PASSWORD") &&
      jsondecode(aws_ecs_task_definition.this["gateway"].container_definitions)[0].secrets[0].valueFrom == "arn:aws:secretsmanager:us-east-1:111122223333:secret:mcpgw-test/gateway-db-password-AbCdEf"
    )
    error_message = "secret values are injected by ECS (valueFrom), never written into task definitions"
  }

  assert {
    condition     = jsondecode(aws_ecs_task_definition.this["db-bootstrap"].container_definitions)[0].user == "70:70"
    error_message = "the bootstrap container must not run as root"
  }

  assert {
    condition = (
      length(jsondecode(aws_ecs_task_definition.this["gateway"].container_definitions)) == 2 &&
      !jsondecode(aws_ecs_task_definition.this["gateway"].container_definitions)[1].essential &&
      length(jsondecode(aws_ecs_task_definition.this["operator-ui"].container_definitions)) == 1 &&
      length(jsondecode(aws_ecs_task_definition.this["db-bootstrap"].container_definitions)) == 1
    )
    error_message = "the telemetry sidecar is opt-in per workload and never essential"
  }
}

run "each_workload_reads_only_its_own_secrets" {
  command = apply

  assert {
    condition     = local.workload_secret_arns["crm-ops"] == tolist(["arn:aws:secretsmanager:us-east-1:111122223333:secret:mcpgw-test/upstream-assertion-key-AbCdEf"])
    error_message = "crm-ops's execution role must cover its own secret only"
  }

  assert {
    condition     = local.workload_secret_arns["db-bootstrap"] == tolist(["arn:aws:secretsmanager:us-east-1:111122223333:secret:rds!db-mock-AbCdEf"])
    error_message = "JSON-key references must be reduced to the secret's ARN for IAM"
  }

  assert {
    condition     = keys(aws_iam_role_policy.execution_secrets) == ["crm-ops", "db-bootstrap", "gateway"]
    error_message = "workloads without secrets get no secret access at all"
  }
}

run "service_connect_and_autoscaling" {
  command = apply

  assert {
    condition = (
      length(aws_ecs_service.this["crm-ops"].service_connect_configuration[0].service) == 1 &&
      length(aws_ecs_service.this["gateway"].service_connect_configuration[0].service) == 1 &&
      length(aws_ecs_service.this["operator-ui"].service_connect_configuration[0].service) == 0
    )
    error_message = "services that others call register a Service Connect endpoint; the console only calls"
  }

  assert {
    condition     = aws_ecs_service.this["crm-ops"].service_connect_configuration[0].service[0].client_alias[0].dns_name == "crm-ops.mcp.internal"
    error_message = "the gateway's upstream URL depends on this name"
  }

  assert {
    condition     = keys(aws_appautoscaling_target.this) == ["gateway"] && aws_appautoscaling_target.this["gateway"].min_capacity == 2 && aws_appautoscaling_target.this["gateway"].max_capacity == 4
    error_message = "autoscaling runs between desired_count and max_count"
  }
}

run "plain_http_only_when_explicitly_allowed" {
  command = apply

  variables {
    certificate_arn     = null
    allow_insecure_http = true
  }

  assert {
    condition     = length(aws_lb_listener.https) == 0 && length(aws_lb_listener.http) == 1 && keys(aws_vpc_security_group_ingress_rule.alb_from_cidrs) == ["10.0.0.0/8:80"]
    error_message = "without a certificate (sandbox) the rules hang off a plain HTTP listener"
  }
}

run "refuses_insecure_http_by_default" {
  command = plan

  variables {
    certificate_arn = null
  }

  expect_failures = [var.allow_insecure_http]
}

run "refuses_unpinned_images" {
  command = plan

  variables {
    services = {
      gateway = { image = "mcp-gateway:latest", container_port = 8000 }
    }
    tasks = {}
  }

  expect_failures = [var.services]
}

run "refuses_unknown_callers" {
  command = plan

  variables {
    services = {
      crm-ops = {
        image                 = "111122223333.dkr.ecr.us-east-1.amazonaws.com/crm-ops-mcp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        container_port        = 8001
        ingress_from_services = ["somebody-else"]
      }
    }
    tasks = {}
  }

  expect_failures = [var.services]
}

run "refuses_a_sidecar_without_an_image" {
  command = plan

  variables {
    otel_sidecar_image = null
  }

  expect_failures = [var.otel_sidecar_image]
}
