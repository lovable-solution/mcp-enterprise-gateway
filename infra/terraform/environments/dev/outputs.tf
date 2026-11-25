output "gateway_url" {
  description = "The gateway's base URL (MCP endpoint at /mcp): the only endpoint agents use."
  value       = local.public_url
}

output "console_url" {
  description = "The operator console (served by the same load balancer, everything outside the gateway's paths)."
  value       = "${local.public_url}/"
}

output "alb_dns_name" {
  description = "The internal load balancer's own DNS name (point gateway_hostname at it if Terraform does not manage the record)."
  value       = module.ecs.alb_dns_name
}

output "exposed_services" {
  description = "Services reachable through the load balancer: the gateway and the console, never an MCP server."
  value       = module.ecs.exposed_services
}

output "alb_routes" {
  description = "Path routing on the load balancer, by priority."
  value       = module.ecs.alb_routes
}

output "secret_names" {
  description = "Secrets Manager names created for this environment."
  value       = module.secrets.secret_names
}

output "rds_master_secret_arn" {
  description = "RDS-managed master credential (read by the db-bootstrap task only)."
  value       = module.rds.master_user_secret_arn
}

output "nat_public_ips" {
  description = "Egress IPs of the environment (allow-list them at the identity provider if it restricts callers)."
  value       = module.vpc.nat_public_ips
}

output "run_task_commands" {
  description = "Start the on-demand tasks: db-bootstrap once after the first apply; agent-runtime with --overrides for a run."
  value       = module.ecs.run_task_commands
}
