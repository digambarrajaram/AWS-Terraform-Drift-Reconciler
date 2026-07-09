# NAT Gateway Module - Variables

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
  description = "VPC ID where NAT Gateways will be created"
  type        = string
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for NAT Gateway placement"
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "At least 2 public subnets required for high availability."
  }
}

variable "private_app_subnet_ids" {
  description = "List of private app subnet IDs that need NAT routing"
  type        = list(string)
}

variable "availability_zones" {
  description = "List of availability zones matching subnet deployment"
  type        = list(string)
}

variable "internet_gateway_id" {
  description = "Internet Gateway ID (for dependency management)"
  type        = string
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
