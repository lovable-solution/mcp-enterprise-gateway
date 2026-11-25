# Output contract. Values are null placeholders until Phase 4 implements the
# resources; names and meanings are fixed so callers can be wired now.

output "vpc_id" {
  description = "ID of the VPC."
  value       = null # Phase 4: aws_vpc.this.id
}

output "vpc_cidr_block" {
  description = "IPv4 CIDR of the VPC."
  value       = var.cidr_block
}

output "availability_zones" {
  description = "AZs the subnets are spread across."
  value       = null # Phase 4: data.aws_availability_zones.available.names, first az_count
}

output "public_subnet_ids" {
  description = "Public subnets (NAT gateways; ALB only when internet-facing)."
  value       = null # Phase 4: aws_subnet.public[*].id
}

output "private_app_subnet_ids" {
  description = "Private subnets for ECS tasks and the internal ALB."
  value       = null # Phase 4: aws_subnet.private_app[*].id
}

output "private_data_subnet_ids" {
  description = "Isolated subnets for RDS and ElastiCache (no internet route)."
  value       = null # Phase 4: aws_subnet.private_data[*].id
}
