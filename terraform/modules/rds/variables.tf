# RDS Module - Variables

variable "resource_prefix" {
  description = "Prefix for all resource names (e.g., MYCOMPANY)"
  type        = string
}

variable "account_id" {
  description = "AWS Account ID for resource naming"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, production)"
  type        = string
}

variable "subnet_ids" {
  description = "List of private DB subnet IDs for RDS deployment"
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "RDS requires at least 2 subnets in different AZs."
  }
}

variable "security_group_id" {
  description = "Security group ID for RDS instance"
  type        = string
}

# Instance Configuration
variable "instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

variable "engine_version" {
  description = "MySQL engine version"
  type        = string
  default     = "8.0.35"
}

# Storage Configuration
variable "allocated_storage" {
  description = "Initial allocated storage in GB"
  type        = number
  default     = 100
}

variable "max_allocated_storage" {
  description = "Maximum storage for autoscaling in GB"
  type        = number
  default     = 500
}

variable "kms_key_id" {
  description = "KMS key ID for storage encryption (uses default aws/rds key if not provided)"
  type        = string
  default     = ""
}

# Database Configuration
variable "database_name" {
  description = "Name of the initial database"
  type        = string
  default     = "appdb"
}

variable "master_username" {
  description = "Master username for RDS"
  type        = string
  default     = "admin"
}

# High Availability
variable "multi_az" {
  description = "Enable Multi-AZ deployment"
  type        = bool
  default     = true
}

variable "preferred_az" {
  description = "Preferred AZ for single-AZ deployment (ignored if multi_az is true)"
  type        = string
  default     = ""
}

# Backup Configuration
variable "backup_retention_days" {
  description = "Number of days to retain automated backups"
  type        = number
  default     = 7

  validation {
    condition     = var.backup_retention_days >= 1 && var.backup_retention_days <= 35
    error_message = "Backup retention must be between 1 and 35 days."
  }
}

variable "backup_window" {
  description = "Preferred backup window (UTC)"
  type        = string
  default     = "03:00-04:00"
}

variable "maintenance_window" {
  description = "Preferred maintenance window (UTC)"
  type        = string
  default     = "sun:04:00-sun:05:00"
}

variable "skip_final_snapshot" {
  description = "Skip final snapshot on deletion (NOT recommended for production)"
  type        = bool
  default     = false
}

# Monitoring and Performance
variable "enable_enhanced_monitoring" {
  description = "Enable enhanced monitoring (1-minute metrics)"
  type        = bool
  default     = true
}

variable "monitoring_role_arn" {
  description = "IAM role ARN for enhanced monitoring"
  type        = string
  default     = ""
}

variable "enable_performance_insights" {
  description = "Enable Performance Insights"
  type        = bool
  default     = true
}

variable "performance_insights_kms_key_id" {
  description = "KMS key ARN for Performance Insights encryption"
  type        = string
  default     = null
}

variable "enable_cloudwatch_alarms" {
  description = "Enable CloudWatch alarms for RDS monitoring"
  type        = bool
  default     = true
}

variable "max_connections_threshold" {
  description = "CloudWatch alarm threshold for database connections"
  type        = number
  default     = 80
}

# Parameter and Option Groups
variable "parameter_group_name" {
  description = "Custom parameter group name (uses default if empty)"
  type        = string
  default     = ""
}

variable "option_group_name" {
  description = "Custom option group name (uses default if empty)"
  type        = string
  default     = ""
}

# Security
variable "secret_recovery_days" {
  description = "Number of days to retain deleted secrets"
  type        = number
  default     = 7
}

# Upgrade and Protection
variable "auto_minor_version_upgrade" {
  description = "Enable automatic minor version upgrades"
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "Enable deletion protection"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
