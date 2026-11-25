# -----------------------------------------------------------------------------
# Module: ecs_fargate
#
#   clients --(alb_ingress_cidrs)--> ALB (internal) --> exposed services
#                                                         |  (Service Connect)
#                                                         v
#                                          east-west-only services (MCP servers)
#
# Services run in private subnets without public IPs, with read-only root
# filesystems, their own security group, their own execution role (which can read
# only the secrets that service declares) and their own task role. On-demand tasks
# (database bootstrap, agent runs) share the same plumbing but accept no traffic.
# -----------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  region     = data.aws_region.current.region
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  https          = var.certificate_arn != null
  listener_ports = local.https ? [443] : [80] # no plaintext listener once TLS is configured

  # Services and on-demand tasks, normalised to one shape: same roles, logs,
  # security groups and task-definition code for both.
  workloads = merge(
    {
      for name, svc in var.services : name => {
        kind                     = "service"
        image                    = svc.image
        cpu                      = svc.cpu
        memory                   = svc.memory
        command                  = svc.command
        user                     = svc.user
        container_port           = svc.container_port
        environment              = svc.environment
        secrets                  = svc.secrets
        egress_to                = svc.egress_to
        alb_access               = false
        task_role_policy_json    = svc.task_role_policy_json
        readonly_root_filesystem = svc.readonly_root_filesystem
        writable_paths           = svc.writable_paths
        otel                     = var.enable_otel_sidecar && svc.otel_sidecar
        stop_timeout             = svc.stop_timeout
      }
    },
    {
      for name, task in var.tasks : name => {
        kind                     = "task"
        image                    = task.image
        cpu                      = task.cpu
        memory                   = task.memory
        command                  = task.command
        user                     = task.user
        container_port           = null
        environment              = task.environment
        secrets                  = task.secrets
        egress_to                = task.egress_to
        alb_access               = task.alb_access
        task_role_policy_json    = task.task_role_policy_json
        readonly_root_filesystem = task.readonly_root_filesystem
        writable_paths           = task.writable_paths
        otel                     = var.enable_otel_sidecar && task.otel_sidecar
        stop_timeout             = 30
      }
    },
  )

  exposed      = { for name, svc in var.services : name => svc if svc.expose_via_alb }
  exposed_keys = sort(keys(local.exposed))
  alb_clients  = { for name, w in local.workloads : name => w if w.alb_access }

  # One listener rule per 5 path patterns (the ALB limit per condition).
  alb_rules = merge({}, [
    for position, name in local.exposed_keys : {
      for chunk, patterns in chunklist(local.exposed[name].alb_path_patterns, 5) :
      "${name}-${chunk}" => {
        service  = name
        patterns = patterns
        priority = coalesce(local.exposed[name].alb_priority, 100 + 10 * position) + chunk
      }
    }
  ]...)

  # Target group names are limited to 32 characters.
  target_group_names = {
    for name in local.exposed_keys : name => (
      length("${var.name}-${name}") <= 32 ? "${var.name}-${name}" : "${substr("${var.name}-${name}", 0, 23)}-${substr(sha1(name), 0, 8)}"
    )
  }

  # East-west paths: <caller> -> <service>, from each service's ingress_from_services.
  east_west = {
    for pair in flatten([
      for target, svc in var.services : [
        for caller in svc.ingress_from_services : {
          key    = "${caller}->${target}"
          caller = caller
          target = target
          port   = svc.container_port
        }
      ]
    ]) : pair.key => pair
  }
  service_connect_servers = toset([for pair in values(local.east_west) : pair.target])

  # Extra egress (data stores): <workload> -> <name> on a port, to a security group.
  extra_egress = {
    for rule in flatten([
      for name, w in local.workloads : [
        for target, egress in w.egress_to : {
          key               = "${name}->${target}"
          workload          = name
          security_group_id = egress.security_group_id
          port              = egress.port
        }
      ]
    ]) : rule.key => rule
  }

  alb_paths = {
    for pair in setproduct(keys(local.alb_clients), local.listener_ports) : "${pair[0]}:${pair[1]}" => {
      workload = pair[0]
      port     = pair[1]
    }
  }

  # IAM resources are secret ARNs; strip an optional ":<json-key>:<stage>:<version>".
  workload_secret_arns = {
    for name, w in local.workloads : name => distinct([
      for value_from in values(w.secrets) : join(":", slice(split(":", value_from), 0, 7))
    ])
  }
}

# --- cluster -----------------------------------------------------------------------------
resource "aws_service_discovery_http_namespace" "this" {
  name        = var.service_connect_namespace
  description = "${var.name}: ECS Service Connect (east-west calls between services)"

  tags = var.tags
}

resource "aws_ecs_cluster" "this" {
  name = "${var.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enhanced"
  }

  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.this.arn
  }

  tags = var.tags
}

# --- load balancer -------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "ALB: listeners open to alb_ingress_cidrs (and in-VPC client tasks) only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-alb" })
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cidrs" {
  for_each = {
    for pair in setproduct(var.alb_ingress_cidrs, local.listener_ports) : "${pair[0]}:${pair[1]}" => {
      cidr = pair[0]
      port = pair[1]
    }
  }

  security_group_id = aws_security_group.alb.id
  description       = "Listener ${each.value.port} from an allowed client network"
  cidr_ipv4         = each.value.cidr
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_tasks" {
  for_each = local.alb_paths

  security_group_id            = aws_security_group.alb.id
  description                  = "Listener ${each.value.port} from the ${each.value.workload} task"
  referenced_security_group_id = aws_security_group.workload[each.value.workload].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

resource "aws_vpc_security_group_egress_rule" "alb_to_services" {
  for_each = local.exposed

  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to ${each.key}"
  referenced_security_group_id = aws_security_group.workload[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.container_port
  to_port                      = each.value.container_port
}

resource "aws_lb" "this" {
  name               = "${var.name}-alb"
  internal           = var.internal_alb
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.alb_subnet_ids

  idle_timeout               = var.alb_idle_timeout_seconds
  drop_invalid_header_fields = true
  enable_deletion_protection = var.alb_deletion_protection

  dynamic "access_logs" {
    for_each = var.alb_access_logs_bucket == null ? [] : [var.alb_access_logs_bucket]

    content {
      bucket  = access_logs.value
      prefix  = var.name
      enabled = true
    }
  }

  tags = var.tags
}

resource "aws_lb_target_group" "this" {
  for_each = local.exposed

  name                 = local.target_group_names[each.key]
  port                 = each.value.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30 # SSE clients reconnect (Last-Event-ID) to another task

  health_check {
    path                = each.value.health_check_path
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = var.tags
}

resource "aws_lb_listener" "https" {
  count = local.https ? 1 : 0

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06" # TLS 1.3, and 1.2 with forward secrecy only
  certificate_arn   = var.certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      status_code  = "404"
      message_body = jsonencode({ title = "Not found", status = 404 })
    }
  }

  tags = var.tags
}

# Plain HTTP exists only in the explicit allow_insecure_http sandbox mode. With a
# certificate there is no port-80 listener at all (not even a redirect).
#trivy:ignore:AWS-0054 -- sandbox-only, refused unless allow_insecure_http = true
resource "aws_lb_listener" "http" {
  count = local.https ? 0 : 1

  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      status_code  = "404"
      message_body = jsonencode({ title = "Not found", status = 404 })
    }
  }

  tags = var.tags
}

resource "aws_lb_listener_rule" "service" {
  for_each = local.alb_rules

  listener_arn = local.https ? aws_lb_listener.https[0].arn : aws_lb_listener.http[0].arn
  priority     = each.value.priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[each.value.service].arn
  }

  condition {
    path_pattern {
      values = each.value.patterns
    }
  }

  tags = var.tags
}

resource "aws_route53_record" "alb" {
  for_each = var.alb_aliases

  zone_id = each.value
  name    = each.key
  type    = "A"

  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}

# --- per-workload security groups ------------------------------------------------------------
resource "aws_security_group" "workload" {
  for_each = local.workloads

  name        = "${var.name}-${each.key}"
  description = "${each.key} (${each.value.kind}): least-privilege ingress and egress"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-${each.key}" })
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  for_each = local.exposed

  security_group_id            = aws_security_group.workload[each.key].id
  description                  = "From the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = each.value.container_port
  to_port                      = each.value.container_port
}

resource "aws_vpc_security_group_ingress_rule" "east_west" {
  for_each = local.east_west

  security_group_id            = aws_security_group.workload[each.value.target].id
  description                  = "From ${each.value.caller} (Service Connect)"
  referenced_security_group_id = aws_security_group.workload[each.value.caller].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

resource "aws_vpc_security_group_egress_rule" "east_west" {
  for_each = local.east_west

  security_group_id            = aws_security_group.workload[each.value.caller].id
  description                  = "To ${each.value.target} (Service Connect)"
  referenced_security_group_id = aws_security_group.workload[each.value.target].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

# AWS APIs (through the VPC endpoints), the IdP's JWKS, model APIs: all HTTPS.
# Destinations on the internet cannot be pinned by CIDR; production adds a domain
# allow-list at the edge (egress proxy or AWS Network Firewall) in front of the NAT.
#trivy:ignore:AWS-0104 -- TLS-only egress; domain filtering belongs at the network edge
resource "aws_vpc_security_group_egress_rule" "https" {
  for_each = local.workloads

  security_group_id = aws_security_group.workload[each.key].id
  description       = "HTTPS (AWS APIs via VPC endpoints, identity provider, model APIs)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "extra" {
  for_each = local.extra_egress

  security_group_id            = aws_security_group.workload[each.value.workload].id
  description                  = "To ${each.key}"
  referenced_security_group_id = each.value.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

resource "aws_vpc_security_group_egress_rule" "to_alb" {
  for_each = local.alb_paths

  security_group_id            = aws_security_group.workload[each.value.workload].id
  description                  = "To the load balancer (port ${each.value.port})"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

# --- logs -----------------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "workload" {
  for_each = local.workloads

  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_kms_key_arn

  tags = var.tags
}

# --- IAM -------------------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    # Confused-deputy guard: only ECS tasks of this account may assume these roles.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:ecs:${local.region}:${local.account_id}:*"]
    }
  }
}

# Execution role: what ECS itself needs to start the task (image pull, logs, secrets).
resource "aws_iam_role" "execution" {
  for_each = local.workloads

  name               = "${var.name}-${each.key}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  for_each = local.workloads

  role       = aws_iam_role.execution[each.key].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  for_each = { for name, w in local.workloads : name => w if length(w.secrets) > 0 }

  statement {
    sid       = "ReadOwnSecretsOnly"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.workload_secret_arns[each.key]
  }

  statement {
    sid       = "DecryptThem"
    actions   = ["kms:Decrypt"]
    resources = [var.secrets_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  for_each = data.aws_iam_policy_document.execution_secrets

  name   = "read-own-secrets"
  role   = aws_iam_role.execution[each.key].id
  policy = each.value.json
}

# Task role: what the application itself may call (nothing, unless granted).
resource "aws_iam_role" "task" {
  for_each = local.workloads

  name               = "${var.name}-${each.key}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

resource "aws_iam_role_policy" "task" {
  for_each = { for name, w in local.workloads : name => w if w.task_role_policy_json != null }

  name   = "application"
  role   = aws_iam_role.task[each.key].id
  policy = each.value.task_role_policy_json
}

data "aws_iam_policy_document" "otel" {
  statement {
    sid = "Traces"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
      "xray:GetSamplingStatisticSummaries",
    ]
    resources = ["*"] # X-Ray has no resource-level permissions for these actions
  }

  statement {
    sid       = "MetricsAsEmbeddedMetricFormat"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups"]
    resources = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/ecs/application/metrics*"]
  }
}

resource "aws_iam_role_policy" "otel" {
  for_each = { for name, w in local.workloads : name => w if w.otel }

  name   = "otel-sidecar"
  role   = aws_iam_role.task[each.key].id
  policy = data.aws_iam_policy_document.otel.json
}

# --- task definitions --------------------------------------------------------------------------
locals {
  app_containers = {
    for name, w in local.workloads : name => {
      for key, value in {
        name                   = name
        image                  = w.image
        essential              = true
        command                = w.command
        user                   = w.user
        readonlyRootFilesystem = w.readonly_root_filesystem
        stopTimeout            = w.stop_timeout
        linuxParameters        = { initProcessEnabled = true }
        environment            = [for key in sort(keys(w.environment)) : { name = key, value = w.environment[key] }]
        secrets                = [for key in sort(keys(w.secrets)) : { name = key, valueFrom = w.secrets[key] }]
        mountPoints = [
          for index, path in w.writable_paths : { sourceVolume = "${name}-rw-${index}", containerPath = path, readOnly = false }
        ]
        portMappings = w.container_port == null ? null : [
          { name = "http", containerPort = w.container_port, protocol = "tcp", appProtocol = "http" }
        ]
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            "awslogs-group"         = aws_cloudwatch_log_group.workload[name].name
            "awslogs-region"        = local.region
            "awslogs-stream-prefix" = name
          }
        }
      } : key => value if value != null # ECS defaults stand in for nulls
    }
  }

  otel_containers = {
    for name, w in local.workloads : name => [
      for enabled in(w.otel ? [true] : []) : {
        name                   = "otel-collector"
        image                  = var.otel_sidecar_image
        essential              = false # telemetry loss must never take the service down
        command                = ["--config=/etc/ecs/ecs-default-config.yaml"]
        readonlyRootFilesystem = true
        memoryReservation      = 64
        mountPoints            = [{ sourceVolume = "${name}-otel-tmp", containerPath = "/tmp", readOnly = false }]
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            "awslogs-group"         = aws_cloudwatch_log_group.workload[name].name
            "awslogs-region"        = local.region
            "awslogs-stream-prefix" = "otel"
          }
        }
      }
    ]
  }

  volumes = {
    for name, w in local.workloads : name => concat(
      [for index, path in w.writable_paths : "${name}-rw-${index}"],
      w.otel ? ["${name}-otel-tmp"] : [],
    )
  }
}

resource "aws_ecs_task_definition" "this" {
  for_each = local.workloads

  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(each.value.cpu)
  memory                   = tostring(each.value.memory)
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.task[each.key].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  # Fargate task storage, mounted where a read-only root filesystem needs scratch space.
  dynamic "volume" {
    for_each = toset(local.volumes[each.key])

    content {
      name = volume.value
    }
  }

  container_definitions = jsonencode(concat([local.app_containers[each.key]], local.otel_containers[each.key]))

  tags = var.tags
}

# --- services ------------------------------------------------------------------------------------
resource "aws_ecs_service" "this" {
  for_each = var.services

  name             = each.key
  cluster          = aws_ecs_cluster.this.arn
  task_definition  = aws_ecs_task_definition.this[each.key].arn
  desired_count    = each.value.desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  propagate_tags                = "SERVICE"
  enable_ecs_managed_tags       = true
  enable_execute_command        = false # no shell into production tasks
  availability_zone_rebalancing = "ENABLED"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = each.value.expose_via_alb ? 60 : null

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.service_subnet_ids
    security_groups  = [aws_security_group.workload[each.key].id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.value.expose_via_alb ? [1] : []

    content {
      target_group_arn = aws_lb_target_group.this[each.key].arn
      container_name   = each.key
      container_port   = each.value.container_port
    }
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    # Services that others may call register an endpoint; every service can call.
    dynamic "service" {
      for_each = contains(local.service_connect_servers, each.key) ? [1] : []

      content {
        port_name      = "http"
        discovery_name = each.key

        client_alias {
          port     = each.value.container_port
          dns_name = "${each.key}.${var.service_connect_namespace}"
        }
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.workload[each.key].name
        "awslogs-region"        = local.region
        "awslogs-stream-prefix" = "service-connect"
      }
    }
  }

  tags = var.tags

  # The target group must be attached to the listener before ECS registers targets,
  # and the execution role must be able to read secrets before the first task starts.
  depends_on = [aws_lb_listener_rule.service, aws_iam_role_policy.execution_secrets, aws_iam_role_policy_attachment.execution]
}

resource "aws_appautoscaling_target" "this" {
  for_each = { for name, svc in var.services : name => svc if svc.max_count != null }

  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.this[each.key].name}"
  min_capacity       = each.value.desired_count
  max_capacity       = each.value.max_count
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each = aws_appautoscaling_target.this

  name               = "${var.name}-${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = each.value.service_namespace
  scalable_dimension = each.value.scalable_dimension
  resource_id        = each.value.resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
