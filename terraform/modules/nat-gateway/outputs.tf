# NAT Gateway Module - Outputs

# NAT Gateway Identifiers
output "nat_gateway_ids" {
  description = "List of NAT Gateway IDs"
  value       = aws_nat_gateway.main[*].id
}

output "nat_gateway_public_ips" {
  description = "List of NAT Gateway public IP addresses"
  value       = aws_eip.nat[*].public_ip
}

output "nat_gateway_private_ips" {
  description = "List of NAT Gateway private IP addresses"
  value       = aws_nat_gateway.main[*].private_ip
}

# NAT Gateway per AZ mapping
output "nat_gateway_az_map" {
  description = "Map of availability zones to NAT Gateway IDs"
  value = {
    for idx, nat in aws_nat_gateway.main :
    element(var.availability_zones, idx) => nat.id
  }
}

# Elastic IP Identifiers
output "eip_ids" {
  description = "List of Elastic IP IDs for NAT Gateways"
  value       = aws_eip.nat[*].id
}

output "eip_allocation_ids" {
  description = "List of Elastic IP allocation IDs"
  value       = aws_eip.nat[*].allocation_id
}

# Route Table Identifiers
output "private_app_route_table_ids" {
  description = "List of private app route table IDs (per-AZ NAT routing)"
  value       = aws_route_table.private_app[*].id
}

output "private_app_route_table_az_map" {
  description = "Map of availability zones to private app route table IDs"
  value = {
    for idx, rt in aws_route_table.private_app :
    element(var.availability_zones, idx) => rt.id
  }
}
