# Security Groups Module - Outputs

# ALB Security Group
output "alb_security_group_id" {
  description = "ID of the ALB security group"
  value       = aws_security_group.alb.id
}

output "alb_security_group_arn" {
  description = "ARN of the ALB security group"
  value       = aws_security_group.alb.arn
}

# EC2 Security Group
output "ec2_security_group_id" {
  description = "ID of the EC2 security group"
  value       = aws_security_group.ec2.id
}

output "ec2_security_group_arn" {
  description = "ARN of the EC2 security group"
  value       = aws_security_group.ec2.arn
}

# RDS Security Group
output "rds_security_group_id" {
  description = "ID of the RDS security group"
  value       = aws_security_group.rds.id
}

output "rds_security_group_arn" {
  description = "ARN of the RDS security group"
  value       = aws_security_group.rds.arn
}

# ElastiCache Security Group
output "elasticache_security_group_id" {
  description = "ID of the ElastiCache security group"
  value       = aws_security_group.elasticache.id
}

output "elasticache_security_group_arn" {
  description = "ARN of the ElastiCache security group"
  value       = aws_security_group.elasticache.arn
}

# Bastion Security Group (if enabled)
output "bastion_security_group_id" {
  description = "ID of the bastion security group (if enabled)"
  value       = var.enable_bastion ? aws_security_group.bastion[0].id : null
}

output "bastion_security_group_arn" {
  description = "ARN of the bastion security group (if enabled)"
  value       = var.enable_bastion ? aws_security_group.bastion[0].arn : null
}

# All Security Group IDs
output "all_security_group_ids" {
  description = "Map of all security group IDs"
  value = {
    alb          = aws_security_group.alb.id
    ec2          = aws_security_group.ec2.id
    rds          = aws_security_group.rds.id
    elasticache  = aws_security_group.elasticache.id
    bastion      = var.enable_bastion ? aws_security_group.bastion[0].id : null
  }
}
