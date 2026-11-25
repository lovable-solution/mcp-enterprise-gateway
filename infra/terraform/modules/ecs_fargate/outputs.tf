# Output contract. Values are null placeholders until Phase 4 implements the
# resources; names and meanings are fixed so callers can be wired now.

output "cluster_name" {
  description = "ECS cluster name."
  value       = "${var.name}-cluster"
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = null # Phase 4: aws_ecs_cluster.this.arn
}

output "alb_dns_name" {
  description = "DNS name of the load balancer (the only entry point)."
  value       = null # Phase 4: aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "Route 53 zone id of the load balancer, for alias records."
  value       = null # Phase 4: aws_lb.this.zone_id
}

output "alb_security_group_id" {
  description = "Security group of the load balancer."
  value       = null # Phase 4: aws_security_group.alb.id
}

output "exposed_services" {
  description = "Services reachable through the load balancer."
  value       = keys(local.exposed_services)
}

output "service_security_group_ids" {
  description = "Security group per service, keyed by service name (grant these access to data stores)."
  value       = null # Phase 4: { for k, sg in aws_security_group.service : k => sg.id }
}

output "task_role_arns" {
  description = "Task (application) IAM role per service."
  value       = null # Phase 4: { for k, r in aws_iam_role.task : k => r.arn }
}

output "execution_role_arns" {
  description = "Task execution IAM role per service (image pull, secret injection, logs)."
  value       = null # Phase 4: { for k, r in aws_iam_role.execution : k => r.arn }
}
