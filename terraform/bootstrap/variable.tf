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

resource "random_string" "suffix" {
  length  = 8
  special = false
  upper   = false
}