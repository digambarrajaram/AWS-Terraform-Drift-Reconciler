# Security Groups Module - Variables

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

variable "vpc_id" {
  description = "VPC ID where security groups will be created"
  type        = string
}

variable "app_port" {
  description = "Application port for EC2 instances"
  type        = number
  default     = 8080
}

variable "enable_bastion" {
  description = "Enable bastion host security group"
  type        = bool
  default     = false
}

variable "bastion_allowed_cidr" {
  description = "CIDR block allowed to SSH to bastion host"
  type        = string
  default     = "0.0.0.0/0"
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
