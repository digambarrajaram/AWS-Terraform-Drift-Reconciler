# VPC Module - Variables

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

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "VPC CIDR must be a valid IPv4 CIDR block."
  }
}

variable "availability_zones" {
  description = "List of availability zones for subnet deployment"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "At least 2 availability zones required for high availability."
  }
}

variable "enable_flow_logs" {
  description = "Enable VPC Flow Logs for network traffic monitoring"
  type        = bool
  default     = true
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

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
