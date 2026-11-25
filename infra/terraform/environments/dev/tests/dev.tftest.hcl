# The whole dev environment, wired end to end (mocked providers: no AWS account).

mock_provider "aws" {
  source = "../../tests/mocks/aws"
}

mock_provider "random" {}

variables {
  allowed_account_ids  = ["111122223333"]
  alb_ingress_cidrs    = ["10.0.0.0/8"]
  certificate_arn      = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  gateway_hostname     = "gateway.example.internal"
  private_zone_id      = "Z0123456789ABCDEFGHIJ"
  gateway_image        = "111122223333.dkr.ecr.us-east-1.amazonaws.com/mcp-gateway@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  crm_ops_image        = "111122223333.dkr.ecr.us-east-1.amazonaws.com/crm-ops-mcp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  operator_ui_image    = "111122223333.dkr.ecr.us-east-1.amazonaws.com/operator-ui@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  agent_runtime_image  = "111122223333.dkr.ecr.us-east-1.amazonaws.com/agent-runtime@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  db_bootstrap_image   = "111122223333.dkr.ecr.us-east-1.amazonaws.com/postgres@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  otel_collector_image = "public.ecr.aws/aws-observability/aws-otel-collector@sha256:1111111111111111111111111111111111111111111111111111111111111111"
  jwt_issuer           = "https://idp.example.com/"
  jwt_jwks_url         = "https://idp.example.com/.well-known/jwks.json"
}

run "the_environment_wires_up" {
  command = apply

  assert {
    condition     = output.exposed_services == tolist(["gateway", "operator-ui"])
    error_message = "the load balancer serves the gateway and the console, never an MCP server"
  }

  assert {
    condition     = output.gateway_url == "https://gateway.example.internal" && output.console_url == "https://gateway.example.internal/"
    error_message = "clients and the agent task use the certificate's name over HTTPS"
  }

  assert {
    condition = (
      output.alb_routes["gateway-0"].priority < output.alb_routes["operator-ui-0"].priority &&
      contains(output.alb_routes["gateway-0"].paths, "/mcp") &&
      contains(output.alb_routes["gateway-0"].paths, "/v1/*")
    )
    error_message = "the gateway's paths must win over the console's catch-all"
  }

  assert {
    condition     = keys(output.run_task_commands) == ["agent-runtime", "db-bootstrap"] && strcontains(output.run_task_commands["db-bootstrap"], "assignPublicIp=DISABLED")
    error_message = "both on-demand tasks start in the private subnets"
  }

  assert {
    condition     = output.secret_names["console-session-secret"] == "mcpgw-dev/console-session-secret"
    error_message = "every credential lives in Secrets Manager under the environment's prefix"
  }
}

run "a_certificate_without_a_hostname_is_flagged" {
  command = plan

  variables {
    gateway_hostname = null
    private_zone_id  = null
  }

  expect_failures = [check.agent_task_tls_name]
}

run "plain_http_sandbox" {
  command = apply

  variables {
    certificate_arn     = null
    allow_insecure_http = true
    gateway_hostname    = null
    private_zone_id     = null
  }

  assert {
    condition     = startswith(output.gateway_url, "http://")
    error_message = "without a certificate the sandbox serves plain HTTP on the ALB's own name"
  }
}
