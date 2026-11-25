# -----------------------------------------------------------------------------
# Module: vpc  (Phase 1: interface only; resources land in Phase 4)
#
# Three subnet tiers per AZ, so that only the load balancer can ever face a
# client network and nothing stateful is routable from outside:
#
#   public        NAT gateway(s); the ALB only if it is internet-facing
#   private-app   ECS Fargate tasks and the internal ALB (no public IPs)
#   private-data  RDS and ElastiCache (no route to the internet at all)
#
# Planned resources:
#   aws_vpc, aws_internet_gateway, aws_subnet (3 tiers x az_count),
#   aws_eip + aws_nat_gateway (single | per_az), aws_route_table(+associations),
#   aws_vpc_endpoint: S3 (gateway) + var.interface_endpoints (interface, private DNS),
#   aws_security_group for the interface endpoints (443 from the VPC CIDR),
#   aws_flow_log -> aws_cloudwatch_log_group (+ IAM role) when retention > 0,
#   aws_default_security_group with no rules (locks down the default SG).
# -----------------------------------------------------------------------------

locals {
  # Address plan (for the default /16): public 10.40.0.0/20, private-data
  # 10.40.16.0/20, private-app 10.40.64.0/18 (Fargate uses one IP per task, so it
  # gets the largest block). 10.40.32.0/19 and 10.40.128.0/17 stay free for growth.
  # Each tier block is later split into one subnet per AZ (cidrsubnet(block, 2, i)).
  tier_cidrs = {
    public       = cidrsubnet(var.cidr_block, 4, 0)
    private_data = cidrsubnet(var.cidr_block, 4, 1)
    private_app  = cidrsubnet(var.cidr_block, 2, 1)
  }
}
