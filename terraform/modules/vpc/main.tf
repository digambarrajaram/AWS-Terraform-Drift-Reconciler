# VPC Module - Main Configuration
# Creates VPC, Subnets (Public, Private App, Private DB), and Internet Gateway

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# VPC
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.environment}-vpc"
      Environment = var.environment
    }
  )
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.environment}-igw"
      Environment = var.environment
    }
  )
}

# Public Subnets (3 AZs)
resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-public-${var.availability_zones[count.index]}"
      Environment = var.environment
      Tier        = "Public"
      AZ          = var.availability_zones[count.index]
    }
  )
}

# Private App Subnets (3 AZs) - 10.0.32.0/19 range
resource "aws_subnet" "private_app" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 32)
  availability_zone = var.availability_zones[count.index]

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-private-app-${var.availability_zones[count.index]}"
      Environment = var.environment
      Tier        = "PrivateApp"
      AZ          = var.availability_zones[count.index]
    }
  )
}

# Private DB Subnets (3 AZs) - 10.0.64.0/19 range
resource "aws_subnet" "private_db" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 64)
  availability_zone = var.availability_zones[count.index]

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-private-db-${var.availability_zones[count.index]}"
      Environment = var.environment
      Tier        = "PrivateDB"
      AZ          = var.availability_zones[count.index]
    }
  )
}

# Public Route Table (shared across all public subnets)
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-public-rt"
      Environment = var.environment
    }
  )
}

# Associate public subnets with public route table
resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Private DB Route Table (shared, NO internet route)
# Security Note: This ensures pure internal routing.
# Traffic logic: 10.0.0.0/16 implicitly routes locally via VPC Router.
resource "aws_route_table" "private_db" {
  vpc_id = aws_vpc.main.id

  # Only local route (10.0.0.0/16 → local) - implicit, no 0.0.0.0/0

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-private-db-rt"
      Environment = var.environment
      Tier        = "PrivateDB"
    }
  )
}

# Associate private DB subnets with DB route table
resource "aws_route_table_association" "private_db" {
  count = length(aws_subnet.private_db)

  subnet_id      = aws_subnet.private_db[count.index].id
  route_table_id = aws_route_table.private_db.id
}

# VPC Flow Logs (for security audit and troubleshooting)
resource "aws_flow_log" "main" {
  count = var.enable_flow_logs ? 1 : 0

  iam_role_arn    = var.flow_log_role_arn
  log_destination = var.flow_log_destination_arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.main.id

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-vpc-flow-logs"
      Environment = var.environment
    }
  )
}

# Default Security Group (Locked down)
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-default-sg"
      Environment = var.environment
      Purpose     = "Default security group locked down for security"
    }
  )
}
