# Compute Module - Variables
variable "aws_region" {
  description = "AWS Region to deploy state resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name prefix"
  type        = string
  default     = "aws-terraform-drift-reconciler"
}

variable "resource_prefix" {
  description = "Prefix for all resource names (e.g., MYCOMPANY)"
  type        = string
  default = "aws-terraform-drift-reconciler"
}

variable "account_id" {
  description = "AWS Account ID for resource naming"
  type        = string
  default = "605134452604"
}

variable "environment" {
  description = "Environment name (dev, staging, production)"
  type        = string
  default = "dev"
}

variable "tags" {
  description = "Map of tags to assign to the resources"
  type        = map(string)
  default     = {}
}

variable "security_group_id" {
  description = "Security group ID for EC2 instances"
  type        = string
  default     = null
}

# Instance Configuration
variable "ami_id" {
  description = "AMI ID for EC2 instances (leave empty to auto-detect latest Amazon Linux 2)"
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t2.micro"
}


