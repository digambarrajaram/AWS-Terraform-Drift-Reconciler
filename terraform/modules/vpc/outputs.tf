# VPC Module - Outputs

# VPC Identifiers
output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "CIDR block of the VPC"
  value       = aws_vpc.main.cidr_block
}

output "vpc_arn" {
  description = "ARN of the VPC"
  value       = aws_vpc.main.arn
}

# Internet Gateway
output "internet_gateway_id" {
  description = "ID of the Internet Gateway"
  value       = aws_internet_gateway.main.id
}

# Public Subnets
output "public_subnet_ids" {
  description = "List of public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "public_subnet_cidrs" {
  description = "List of public subnet CIDR blocks"
  value       = aws_subnet.public[*].cidr_block
}

output "public_subnet_azs" {
  description = "Map of public subnet IDs to availability zones"
  value = {
    for subnet in aws_subnet.public :
    subnet.id => subnet.availability_zone
  }
}

# Private App Subnets
output "private_app_subnet_ids" {
  description = "List of private application subnet IDs"
  value       = aws_subnet.private_app[*].id
}

output "private_app_subnet_cidrs" {
  description = "List of private application subnet CIDR blocks"
  value       = aws_subnet.private_app[*].cidr_block
}

output "private_app_subnet_azs" {
  description = "Map of private app subnet IDs to availability zones"
  value = {
    for subnet in aws_subnet.private_app :
    subnet.id => subnet.availability_zone
  }
}

# Private DB Subnets
output "private_db_subnet_ids" {
  description = "List of private database subnet IDs"
  value       = aws_subnet.private_db[*].id
}

output "private_db_subnet_cidrs" {
  description = "List of private database subnet CIDR blocks"
  value       = aws_subnet.private_db[*].cidr_block
}

output "private_db_subnet_azs" {
  description = "Map of private DB subnet IDs to availability zones"
  value = {
    for subnet in aws_subnet.private_db :
    subnet.id => subnet.availability_zone
  }
}

# Route Tables
output "public_route_table_id" {
  description = "ID of the public route table"
  value       = aws_route_table.public.id
}

output "private_db_route_table_id" {
  description = "ID of the private database route table"
  value       = aws_route_table.private_db.id
}

# Flow Logs
output "flow_log_id" {
  description = "ID of the VPC Flow Log (if enabled)"
  value       = var.enable_flow_logs ? aws_flow_log.main[0].id : null
}
