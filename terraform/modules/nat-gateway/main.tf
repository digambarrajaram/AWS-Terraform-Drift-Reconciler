# NAT Gateway Module - Main Configuration

terraform {
  required_version = ">= 1.6.0"
}

# Elastic IP for NAT Gateway in each AZ
resource "aws_eip" "nat" {
  count  = length(var.public_subnet_ids)
  domain = "vpc"

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-nat-eip-${count.index + 1}"
      Environment = var.environment
      Purpose     = "NAT Gateway"
      AZ          = element(var.availability_zones, count.index)
    },
    var.tags
  )

  depends_on = [var.internet_gateway_id]
}

# NAT Gateway in each public subnet (one per AZ)
resource "aws_nat_gateway" "main" {
  count         = length(var.public_subnet_ids)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = element(var.public_subnet_ids, count.index)

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-nat-gateway-${count.index + 1}"
      Environment = var.environment
      Purpose     = "Outbound internet access for private subnets"
      AZ          = element(var.availability_zones, count.index)
    },
    var.tags
  )

  depends_on = [var.internet_gateway_id]
}

# Route table for each private app subnet (per-AZ NAT routing)
# Architecture Note: Defines OUTBOUND-ONLY path. Inbound traffic bypasses NAT.
resource "aws_route_table" "private_app" {
  count  = length(var.private_app_subnet_ids)
  vpc_id = var.vpc_id

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-private-app-rt-${count.index + 1}"
      Environment = var.environment
      Type        = "Private"
      Purpose     = "Private app subnet routing with NAT"
      AZ          = element(var.availability_zones, count.index)
    },
    var.tags
  )
}

# Default route to NAT Gateway for each private app subnet
resource "aws_route" "private_app_nat" {
  count                  = length(var.private_app_subnet_ids)
  route_table_id         = aws_route_table.private_app[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[count.index].id
}

# Associate each private app subnet with its per-AZ route table
resource "aws_route_table_association" "private_app" {
  count          = length(var.private_app_subnet_ids)
  subnet_id      = element(var.private_app_subnet_ids, count.index)
  route_table_id = aws_route_table.private_app[count.index].id
}
