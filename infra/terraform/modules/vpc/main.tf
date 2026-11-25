# -----------------------------------------------------------------------------
# Module: vpc
#
# Three subnet tiers per AZ, so that only the load balancer can ever face a
# client network and nothing stateful is routable from outside:
#
#   public        NAT gateway(s); the ALB only if it is internet-facing
#   private-app   ECS Fargate tasks and the internal ALB (no public IPs);
#                 egress through NAT, AWS APIs through VPC endpoints
#   private-data  RDS and ElastiCache: local routes only, no internet path at all
# -----------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"] # no Local Zones / Wavelength
  }
}

locals {
  # Address plan (for the default /16): public 10.40.0.0/20, private-data
  # 10.40.16.0/20, private-app 10.40.64.0/18 (Fargate uses one IP per task, so it
  # gets the largest block). 10.40.32.0/19 and 10.40.128.0/17 stay free for growth.
  # Each tier block is split into one subnet per AZ (cidrsubnet(block, 2, i)).
  tier_cidrs = {
    public       = cidrsubnet(var.cidr_block, 4, 0)
    private_data = cidrsubnet(var.cidr_block, 4, 1)
    private_app  = cidrsubnet(var.cidr_block, 2, 1)
  }

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  subnets = {
    for tier, block in local.tier_cidrs : tier => {
      for index, az in local.azs : az => cidrsubnet(block, 2, index)
    }
  }

  nat_azs = var.nat_gateway_mode == "per_az" ? local.azs : [local.azs[0]]

  region = data.aws_region.current.region
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true # interface endpoints and Service Connect need private DNS
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = var.name })
}

# The default security group allows all traffic between its members. Owning it here
# without any rules removes them, so nothing can fall back to it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-default-deny-all" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = var.name })
}

# --- subnets ---------------------------------------------------------------------------
resource "aws_subnet" "public" {
  for_each = local.subnets.public

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false # only NAT gateways (Elastic IPs) live here

  tags = merge(var.tags, { Name = "${var.name}-public-${each.key}", Tier = "public" })
}

resource "aws_subnet" "private_app" {
  for_each = local.subnets.private_app

  vpc_id            = aws_vpc.this.id
  availability_zone = each.key
  cidr_block        = each.value

  tags = merge(var.tags, { Name = "${var.name}-app-${each.key}", Tier = "private-app" })
}

resource "aws_subnet" "private_data" {
  for_each = local.subnets.private_data

  vpc_id            = aws_vpc.this.id
  availability_zone = each.key
  cidr_block        = each.value

  tags = merge(var.tags, { Name = "${var.name}-data-${each.key}", Tier = "private-data" })
}

# --- egress ------------------------------------------------------------------------------
resource "aws_eip" "nat" {
  for_each = toset(local.nat_azs)

  domain = "vpc"

  tags = merge(var.tags, { Name = "${var.name}-nat-${each.key}" })
}

resource "aws_nat_gateway" "this" {
  for_each = toset(local.nat_azs)

  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = aws_subnet.public[each.key].id

  tags = merge(var.tags, { Name = "${var.name}-${each.key}" })

  depends_on = [aws_internet_gateway.this]
}

# --- routing -----------------------------------------------------------------------------
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(var.tags, { Name = "${var.name}-public" })
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# One table per AZ, so "per_az" keeps each AZ's egress inside that AZ.
resource "aws_route_table" "private_app" {
  for_each = toset(local.azs)

  vpc_id = aws_vpc.this.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[var.nat_gateway_mode == "per_az" ? each.key : local.azs[0]].id
  }

  tags = merge(var.tags, { Name = "${var.name}-app-${each.key}" })
}

resource "aws_route_table_association" "private_app" {
  for_each = aws_subnet.private_app

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private_app[each.key].id
}

# Data tier: the implicit local route only. Databases cannot reach (or be reached
# from) anything outside the VPC, whatever their security groups say.
resource "aws_route_table" "private_data" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-data" })
}

resource "aws_route_table_association" "private_data" {
  for_each = aws_subnet.private_data

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private_data.id
}

# --- VPC endpoints: AWS APIs without traversing the NAT gateway ----------------------------
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for az in local.azs : aws_route_table.private_app[az].id] # ECR image layers

  tags = merge(var.tags, { Name = "${var.name}-s3" })
}

resource "aws_security_group" "endpoints" {
  name        = "${var.name}-vpc-endpoints"
  description = "Interface VPC endpoints: HTTPS from inside the VPC only."
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-vpc-endpoints" })
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_https" {
  security_group_id = aws_security_group.endpoints.id
  description       = "HTTPS from the VPC"
  cidr_ipv4         = var.cidr_block
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_endpoint" "interface" {
  for_each = var.interface_endpoints

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${local.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [for az in local.azs : aws_subnet.private_app[az].id]
  security_group_ids  = [aws_security_group.endpoints.id]

  tags = merge(var.tags, { Name = "${var.name}-${each.key}" })
}

# --- flow logs ---------------------------------------------------------------------------
locals {
  flow_logs = var.flow_logs_retention_days > 0
}

resource "aws_cloudwatch_log_group" "flow_logs" {
  count = local.flow_logs ? 1 : 0

  name              = "/vpc/${var.name}/flow-logs"
  retention_in_days = var.flow_logs_retention_days
  kms_key_id        = var.logs_kms_key_arn

  tags = var.tags
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }

    # Confused-deputy guard: only flow logs of this account may assume the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:ec2:${local.region}:${data.aws_caller_identity.current.account_id}:vpc-flow-log/*"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  count = local.flow_logs ? 1 : 0

  name               = "${var.name}-vpc-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "flow_logs_write" {
  count = local.flow_logs ? 1 : 0

  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.flow_logs[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  count = local.flow_logs ? 1 : 0

  name   = "write-flow-logs"
  role   = aws_iam_role.flow_logs[0].id
  policy = data.aws_iam_policy_document.flow_logs_write[0].json
}

resource "aws_flow_log" "this" {
  count = local.flow_logs ? 1 : 0

  vpc_id                   = aws_vpc.this.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs[0].arn
  iam_role_arn             = aws_iam_role.flow_logs[0].arn
  max_aggregation_interval = 60

  tags = merge(var.tags, { Name = var.name })
}
