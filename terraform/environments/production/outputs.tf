# Production Environment - Outputs

# VPC Outputs
output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "vpc_cidr" {
  description = "CIDR block of the VPC"
  value       = module.vpc.vpc_cidr
}

output "public_subnet_ids" {
  description = "List of public subnet IDs"
  value       = module.vpc.public_subnet_ids
}

output "private_app_subnet_ids" {
  description = "List of private app subnet IDs"
  value       = module.vpc.private_app_subnet_ids
}

output "private_db_subnet_ids" {
  description = "List of private DB subnet IDs"
  value       = module.vpc.private_db_subnet_ids
}

# NAT Gateway Outputs
output "nat_gateway_public_ips" {
  description = "Public IP addresses of NAT Gateways"
  value       = module.nat_gateway.nat_gateway_public_ips
}

# ALB Outputs
output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = module.alb.alb_dns_name
}

output "alb_zone_id" {
  description = "Route 53 zone ID of the ALB"
  value       = module.alb.alb_zone_id
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer"
  value       = module.alb.alb_arn
}

# RDS Outputs
output "rds_endpoint" {
  description = "RDS instance endpoint"
  value       = module.rds.db_endpoint
}

output "rds_database_name" {
  description = "Name of the RDS database"
  value       = module.rds.db_name
}

output "rds_credentials_secret_arn" {
  description = "ARN of Secrets Manager secret containing RDS credentials"
  value       = module.rds.credentials_secret_arn
}

# ElastiCache Outputs
output "redis_configuration_endpoint" {
  description = "Redis cluster configuration endpoint"
  value       = module.elasticache.configuration_endpoint
}

output "redis_auth_secret_arn" {
  description = "ARN of Secrets Manager secret containing Redis AUTH token"
  value       = module.elasticache.auth_secret_arn
}

# Auto Scaling Outputs
output "autoscaling_group_name" {
  description = "Name of the Auto Scaling Group"
  value       = module.compute.autoscaling_group_name
}

output "autoscaling_group_arn" {
  description = "ARN of the Auto Scaling Group"
  value       = module.compute.autoscaling_group_arn
}

# Security Group Outputs
output "security_group_ids" {
  description = "Map of all security group IDs"
  value       = module.security_groups.all_security_group_ids
}

# CloudWatch Log Groups
output "cloudwatch_log_groups" {
  description = "CloudWatch Log Group names"
  value = {
    application      = aws_cloudwatch_log_group.app.name
    redis_slow_log   = aws_cloudwatch_log_group.redis_slow_log.name
    redis_engine_log = aws_cloudwatch_log_group.redis_engine_log.name
  }
}

# Deployment Information
output "deployment_info" {
  description = "Deployment information and next steps"
  value       = <<-EOT
    
    ========================================
    AWS Networking Architecture - Production
    ========================================
    
    Environment:  ${var.environment}
    Region:       ${var.aws_region}
    VPC CIDR:     ${module.vpc.vpc_cidr}
    
    APPLICATION ACCESS:
    - ALB DNS Name: ${module.alb.alb_dns_name}
    - Health Check: http://${module.alb.alb_dns_name}${var.health_check_path}
    
    IMPORTANT NEXT STEPS:
    
    1. Route 53 DNS Configuration:
       - Create an A record (alias) pointing to ALB:
         ${module.alb.alb_dns_name} (Zone ID: ${module.alb.alb_zone_id})
    
    2. ACM Certificate (if HTTPS not configured):
       - Request certificate in ACM for your domain
       - Update variable 'acm_certificate_arn' in terraform.tfvars
       - Re-apply Terraform to enable HTTPS
    
    3. Database Credentials:
       - RDS credentials stored in: ${module.rds.credentials_secret_arn}
       - Redis AUTH token stored in: ${module.elasticache.auth_secret_arn}
       - Access via AWS Secrets Manager or IAM-enabled applications
    
    4. Application Deployment:
       - Deploy application code to EC2 instances via CI/CD pipeline
       - Application will auto-discover RDS and Redis endpoints via Secrets Manager
    
    5. Monitoring Setup:
       - Configure SNS topics for CloudWatch alarm notifications
       - Review CloudWatch dashboards for metrics
       - Set up log analysis with CloudWatch Insights
    
    6. Security Hardening:
       - Review and restrict security group rules as needed
       - Configure AWS WAF rules on ALB if required
       - Enable AWS Config for compliance monitoring
       - Set up AWS GuardDuty for threat detection
    
    7. Backup Verification:
       - Verify RDS automated backups (retention: ${var.rds_backup_retention_days} days)
       - Verify ElastiCache snapshots (retention: ${var.redis_snapshot_retention_days} days)
       - Test restore procedures
    
    RESOURCE COUNTS:
    - Availability Zones: 3
    - Public Subnets: 3
    - Private App Subnets: 3
    - Private DB Subnets: 3
    - NAT Gateways: 3
    - RDS: Multi-AZ (2 instances)
    - ElastiCache: ${var.redis_num_shards} shards × ${var.redis_replicas_per_shard + 1} nodes = ${var.redis_num_shards * (var.redis_replicas_per_shard + 1)} Redis nodes
    - EC2 Auto Scaling: ${var.asg_min_size}-${var.asg_max_size} instances (current: ${var.asg_desired_capacity})
    
    ========================================
  EOT
}
