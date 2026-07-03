# Compute Module - Variables

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
  description = "List of private app subnet IDs for EC2 instances"
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "Auto Scaling Group requires at least 2 subnets in different AZs."
  }
}

variable "security_group_id" {
  description = "Security group ID for EC2 instances"
  type        = string
}

variable "target_group_arn" {
  description = "ARN of the ALB target group"
  type        = string
}

# Instance Configuration
variable "ami_id" {
  description = "AMI ID for EC2 instances (Amazon Linux 2023 recommended)"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "root_volume_size" {
  description = "Size of root EBS volume in GB"
  type        = number
  default     = 30
}

variable "enable_detailed_monitoring" {
  description = "Enable detailed CloudWatch monitoring (1-minute intervals)"
  type        = bool
  default     = true
}

# Auto Scaling Configuration
variable "min_size" {
  description = "Minimum number of instances in Auto Scaling Group"
  type        = number
  default     = 3

  validation {
    condition     = var.min_size >= 1
    error_message = "Minimum size must be at least 1."
  }
}

variable "max_size" {
  description = "Maximum number of instances in Auto Scaling Group"
  type        = number
  default     = 12

  validation {
    condition     = var.max_size >= 1
    error_message = "Maximum size must be at least 1."
  }
}

variable "desired_capacity" {
  description = "Desired number of instances in Auto Scaling Group"
  type        = number
  default     = 6

  validation {
    condition     = var.desired_capacity >= 1
    error_message = "Desired capacity must be at least 1."
  }
}

# Auto Scaling Policies
variable "cpu_target_value" {
  description = "Target CPU utilization for Auto Scaling (percentage)"
  type        = number
  default     = 70

  validation {
    condition     = var.cpu_target_value > 0 && var.cpu_target_value <= 100
    error_message = "CPU target value must be between 1 and 100."
  }
}

variable "request_count_target_value" {
  description = "Target request count per instance for Auto Scaling"
  type        = number
  default     = 1000
}

variable "alb_target_group_label" {
  description = "ALB target group label for request count metric (format: app/alb-name/xxx/targetgroup/tg-name/xxx)"
  type        = string
}

# Application Configuration
variable "app_port" {
  description = "Application port"
  type        = number
  default     = 8080
}

variable "cloudwatch_log_group" {
  description = "CloudWatch Log Group name for application logs"
  type        = string
}

# Secrets and IAM
variable "rds_secret_arn" {
  description = "ARN of RDS credentials in Secrets Manager"
  type        = string
}

variable "redis_secret_arn" {
  description = "ARN of Redis AUTH token in Secrets Manager"
  type        = string
  default     = ""
}

variable "secrets_arns" {
  description = "List of Secrets Manager ARNs that EC2 instances need access to"
  type        = list(string)
}

variable "kms_key_arns" {
  description = "List of KMS key ARNs for secret decryption"
  type        = list(string)
  default     = []
}

# Monitoring
variable "enable_cloudwatch_alarms" {
  description = "Enable CloudWatch alarms for Auto Scaling monitoring"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
