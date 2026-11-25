# -----------------------------------------------------------------------------
# Module: ecs_fargate  (Phase 1: interface only; resources land in Phase 4)
#
#   clients --(alb_ingress_cidrs)--> ALB --> gateway --(Service Connect)--> crm-ops
#                                             |  \--> RDS, ElastiCache
#                                             \--> OTel sidecar --> X-Ray / CloudWatch
#
# Planned resources:
#   aws_ecs_cluster (Container Insights) + Service Connect namespace (Cloud Map),
#   aws_lb (internal by default, drop_invalid_header_fields, deletion protection,
#     idle_timeout) + HTTPS listener (TLS 1.3 policy) + HTTP->HTTPS redirect,
#   aws_lb_target_group (ip targets) + listener rules for exposed services only,
#   security groups: ALB (ingress from alb_ingress_cidrs), one per service
#     (ingress only from the ALB SG for exposed services, otherwise only from the
#     SGs of ingress_from_services),
#   per service: aws_cloudwatch_log_group, task execution role (ECR pull, its own
#     secrets + kms:Decrypt), task role (+ task_role_policy_json),
#     aws_ecs_task_definition (awsvpc, non-root, read-only root filesystem,
#     `secrets` valueFrom, optional ADOT sidecar), aws_ecs_service (private
#     subnets, no public IP, deployment circuit breaker with rollback,
#     Service Connect), target-tracking autoscaling on CPU.
# -----------------------------------------------------------------------------

locals {
  exposed_services = { for name, svc in var.services : name => svc if svc.expose_via_alb }
}
