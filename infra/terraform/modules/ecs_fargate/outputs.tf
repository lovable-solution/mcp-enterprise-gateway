output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "alb_dns_name" {
  description = "DNS name of the load balancer (the only entry point)."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "Route 53 zone id of the load balancer, for alias records."
  value       = aws_lb.this.zone_id
}

output "alb_security_group_id" {
  description = "Security group of the load balancer."
  value       = aws_security_group.alb.id
}

output "exposed_services" {
  description = "Services reachable through the load balancer."
  value       = local.exposed_keys
}

output "alb_routes" {
  description = "Listener rules: priority and path patterns per exposed service (lowest priority evaluated first)."
  value       = { for key, rule in local.alb_rules : key => { service = rule.service, priority = rule.priority, paths = rule.patterns } }
}

output "service_security_group_ids" {
  description = "Security group per service, keyed by service name (grant these access to data stores)."
  value       = { for name in keys(var.services) : name => aws_security_group.workload[name].id }
}

output "task_security_group_ids" {
  description = "Security group per on-demand task, keyed by task name."
  value       = { for name in keys(var.tasks) : name => aws_security_group.workload[name].id }
}

output "task_role_arns" {
  description = "Task (application) IAM role per workload."
  value       = { for name, role in aws_iam_role.task : name => role.arn }
}

output "execution_role_arns" {
  description = "Task execution IAM role per workload (image pull, secret injection, logs)."
  value       = { for name, role in aws_iam_role.execution : name => role.arn }
}

output "task_definition_arns" {
  description = "Task definition ARN per workload."
  value       = { for name, definition in aws_ecs_task_definition.this : name => definition.arn }
}

output "run_task_commands" {
  description = "AWS CLI command that starts each on-demand task in the private subnets (append --overrides to change its command)."
  value = {
    for name in keys(var.tasks) : name => join(" ", [
      "aws ecs run-task",
      "--cluster ${aws_ecs_cluster.this.name}",
      "--task-definition ${aws_ecs_task_definition.this[name].family}",
      "--launch-type FARGATE",
      "--network-configuration 'awsvpcConfiguration={subnets=[${join(",", var.service_subnet_ids)}],securityGroups=[${aws_security_group.workload[name].id}],assignPublicIp=DISABLED}'",
    ])
  }
}
