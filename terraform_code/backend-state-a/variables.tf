# bootstrap/variables.tf
# Shared across state-backend.tf, oidc-provider.tf, scan-role.tf, apply-role.tf

variable "account_label" {
  description = "Short label for this account"
  type        = string
  default = "scope-a"
}

variable "aws_region" {
  description = "AWS region for this account"
  type        = string
  default      = "us-east-1"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for this account's tfstate"
  type        = string
  default     = "scope-a-tf-state-605134452604"
}

variable "lock_table_name" {
  type    = string
  default = "terraform-locks"
}



variable "managed_resource_prefix" {
  description = "Naming prefix used to scope S3/DynamoDB write permissions for the apply role to only resources this project manages (not the whole account)"
  type        = string
  default = "scope-a-"
}

