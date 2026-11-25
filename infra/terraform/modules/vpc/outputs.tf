output "vpc_id" {
  description = "ID of the VPC."
  value       = aws_vpc.this.id
}

output "vpc_cidr_block" {
  description = "IPv4 CIDR of the VPC."
  value       = aws_vpc.this.cidr_block
}

output "availability_zones" {
  description = "AZs the subnets are spread across."
  value       = local.azs
}

output "public_subnet_ids" {
  description = "Public subnets (NAT gateways; ALB only when internet-facing)."
  value       = [for az in local.azs : aws_subnet.public[az].id]
}

output "private_app_subnet_ids" {
  description = "Private subnets for ECS tasks and the internal ALB."
  value       = [for az in local.azs : aws_subnet.private_app[az].id]
}

output "private_data_subnet_ids" {
  description = "Isolated subnets for RDS and ElastiCache (no internet route)."
  value       = [for az in local.azs : aws_subnet.private_data[az].id]
}

output "nat_public_ips" {
  description = "Egress IPs of the NAT gateways (allow-list these at third parties such as the IdP)."
  value       = [for az in local.nat_azs : aws_eip.nat[az].public_ip]
}
