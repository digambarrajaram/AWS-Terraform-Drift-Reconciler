# ElastiCache Module - Variables

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
  description = "List of private DB subnet IDs for ElastiCache deployment"
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "ElastiCache requires at least 2 subnets in different AZs."
  }
}

variable "security_group_id" {
  description = "Security group ID for ElastiCache cluster"
  type        = string
}

# Instance Configuration
variable "node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.r7g.large"
}

variable "engine_version" {
  description = "Redis engine version"
  type        = string
  default     = "7.1"
}

# Cluster Configuration
variable "num_shards" {
  description = "Number of shards (node groups) for Redis cluster"
  type        = number
  default     = 3

  validation {
    condition     = var.num_shards >= 1 && var.num_shards <= 500
    error_message = "Number of shards must be between 1 and 500."
  }
}

variable "replicas_per_shard" {
  description = "Number of replica nodes per shard"
  type        = number
  default     = 2

  validation {
    condition     = var.replicas_per_shard >= 0 && var.replicas_per_shard <= 5
    error_message = "Replicas per shard must be between 0 and 5."
  }
}

# High Availability
variable "automatic_failover_enabled" {
  description = "Enable automatic failover for Multi-AZ"
  type        = bool
  default     = true
}

variable "multi_az_enabled" {
  description = "Enable Multi-AZ deployment"
  type        = bool
  default     = true
}

# Encryption
variable "transit_encryption_enabled" {
  description = "Enable in-transit encryption (TLS)"
  type        = bool
  default     = true
}

variable "auth_token_enabled" {
  description = "Enable Redis AUTH token authentication"
  type        = bool
  default     = true
}

variable "kms_key_id" {
  description = "KMS key ID for at-rest encryption (uses default aws/elasticache key if not provided)"
  type        = string
  default     = ""
}

# Maintenance and Backup
variable "maintenance_window" {
  description = "Preferred maintenance window (UTC)"
  type        = string
  default     = "sun:05:00-sun:06:00"
}

variable "snapshot_window" {
  description = "Preferred snapshot window (UTC)"
  type        = string
  default     = "02:00-03:00"
}

variable "snapshot_retention_days" {
  description = "Number of days to retain automatic snapshots"
  type        = number
  default     = 5

  validation {
    condition     = var.snapshot_retention_days >= 0 && var.snapshot_retention_days <= 35
    error_message = "Snapshot retention must be between 0 and 35 days."
  }
}

variable "auto_minor_version_upgrade" {
  description = "Enable automatic minor version upgrades"
  type        = bool
  default     = true
}

# Logging
variable "slow_log_group_name" {
  description = "CloudWatch Log Group name for slow logs"
  type        = string
  default     = ""
}

variable "engine_log_group_name" {
  description = "CloudWatch Log Group name for engine logs"
  type        = string
  default     = ""
}

# Monitoring
variable "enable_cloudwatch_alarms" {
  description = "Enable CloudWatch alarms for ElastiCache monitoring"
  type        = bool
  default     = true
}

variable "sns_topic_arn" {
  description = "SNS topic ARN for ElastiCache notifications"
  type        = string
  default     = ""
}

# Security
variable "secret_recovery_days" {
  description = "Number of days to retain deleted secrets"
  type        = number
  default     = 7
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
