# Production Environment - Variables

# General Configuration
variable "resource_prefix" {
  description = "Prefix for all resource names (e.g., MYCOMPANY, ACME)"
  type        = string
}

variable "project_name" {
  description = "Name of the project"
  type        = string
  default     = "aws-terraform-drift-reconciler"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "aws_region" {
  description = "AWS region for resource deployment"
  type        = string
  default     = "us-east-1"
}

variable "cost_center" {
  description = "Cost center for billing and tracking"
  type        = string
  default     = "Engineering"
}

# VPC Configuration
variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "flow_log_role_arn" {
  description = "IAM role ARN for VPC Flow Logs"
  type        = string
  default     = ""
}

variable "flow_log_destination_arn" {
  description = "CloudWatch Log Group ARN for VPC Flow Logs"
  type        = string
  default     = ""
}

# Application Configuration
variable "app_port" {
  description = "Application port"
  type        = number
  default     = 8080
}

variable "health_check_path" {
  description = "Health check path for ALB target group"
  type        = string
  default     = "/health"
}

# ALB Configuration
variable "acm_certificate_arn" {
  description = "ARN of ACM certificate for HTTPS"
  type        = string
  default     = ""
}

variable "enable_alb_deletion_protection" {
  description = "Enable deletion protection for ALB"
  type        = bool
  default     = true
}

# Bastion Configuration
variable "enable_bastion" {
  description = "Enable bastion host"
  type        = bool
  default     = false
}

variable "bastion_allowed_cidr" {
  description = "CIDR block allowed to SSH to bastion"
  type        = string
  default     = "0.0.0.0/0"
}

# RDS Configuration
variable "rds_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.r6g.xlarge"
}

variable "rds_engine_version" {
  description = "MySQL engine version"
  type        = string
  default     = "8.0.35"
}

variable "rds_allocated_storage" {
  description = "Initial allocated storage in GB"
  type        = number
  default     = 200
}

variable "rds_max_allocated_storage" {
  description = "Maximum storage for autoscaling in GB"
  type        = number
  default     = 1000
}

variable "rds_database_name" {
  description = "Name of the initial database"
  type        = string
  default     = "productiondb"
}

variable "rds_master_username" {
  description = "Master username for RDS"
  type        = string
  default     = "admin"
}

variable "rds_backup_retention_days" {
  description = "Number of days to retain automated backups"
  type        = number
  default     = 30
}

variable "rds_backup_window" {
  description = "Preferred backup window (UTC)"
  type        = string
  default     = "03:00-04:00"
}

variable "rds_maintenance_window" {
  description = "Preferred maintenance window (UTC)"
  type        = string
  default     = "sun:04:00-sun:05:00"
}

variable "enable_rds_deletion_protection" {
  description = "Enable deletion protection for RDS"
  type        = bool
  default     = true
}

# ElastiCache Configuration
variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.r7g.xlarge"
}

variable "redis_engine_version" {
  description = "Redis engine version"
  type        = string
  default     = "7.1"
}

variable "redis_num_shards" {
  description = "Number of shards for Redis cluster"
  type        = number
  default     = 3
}

variable "redis_replicas_per_shard" {
  description = "Number of replicas per shard"
  type        = number
  default     = 2
}

variable "redis_snapshot_retention_days" {
  description = "Number of days to retain Redis snapshots"
  type        = number
  default     = 7
}

variable "redis_maintenance_window" {
  description = "Preferred maintenance window for Redis (UTC)"
  type        = string
  default     = "sun:05:00-sun:06:00"
}

variable "redis_snapshot_window" {
  description = "Preferred snapshot window for Redis (UTC)"
  type        = string
  default     = "02:00-03:00"
}

variable "redis_slow_log_group" {
  description = "CloudWatch Log Group for Redis slow logs"
  type        = string
  default     = "/aws/elasticache/production/redis/slow-log"
}

variable "redis_engine_log_group" {
  description = "CloudWatch Log Group for Redis engine logs"
  type        = string
  default     = "/aws/elasticache/production/redis/engine-log"
}

# EC2 and Auto Scaling Configuration
variable "ec2_instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.xlarge"
}

variable "ec2_root_volume_size" {
  description = "Size of root EBS volume in GB"
  type        = number
  default     = 50
}

variable "asg_min_size" {
  description = "Minimum number of instances"
  type        = number
  default     = 6
}

variable "asg_max_size" {
  description = "Maximum number of instances"
  type        = number
  default     = 24
}

variable "asg_desired_capacity" {
  description = "Desired number of instances"
  type        = number
  default     = 9
}

variable "asg_cpu_target" {
  description = "Target CPU utilization for Auto Scaling"
  type        = number
  default     = 70
}

variable "asg_request_count_target" {
  description = "Target request count per instance"
  type        = number
  default     = 1000
}

# CloudWatch Configuration
variable "cloudwatch_log_group" {
  description = "CloudWatch Log Group for application logs"
  type        = string
  default     = "/aws/application/production"
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days"
  type        = number
  default     = 90
}
