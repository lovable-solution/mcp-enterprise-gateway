output "gateway_url" {
  description = "The gateway's base URL: the only endpoint agents and the operator console use."
  value       = "${var.certificate_arn != null ? "https" : "http"}://${module.ecs.alb_dns_name}"
}

output "exposed_services" {
  description = "Services reachable through the load balancer (must be the gateway alone)."
  value       = module.ecs.exposed_services
}

output "secret_names" {
  description = "Secrets Manager names created for this environment."
  value       = module.secrets.secret_names
}

output "rds_master_secret_arn" {
  description = "RDS-managed master credential (for the migration task that creates service roles)."
  value       = module.rds.master_user_secret_arn
}
