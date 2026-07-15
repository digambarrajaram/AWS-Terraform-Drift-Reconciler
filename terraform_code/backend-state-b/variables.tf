# bootstrap/variables.tf
# Shared across state-backend.tf, oidc-provider.tf, scan-role.tf, apply-role.tf

variable "account_label" {
  description = "Short label for this account, e.g. 'prod-a', 'prod-b'"
  type        = string
  default = "scope-b"
}

variable "aws_region" {
  description = "AWS region for this account"
  type        = string
  default      = "us-west-2"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for this account's tfstate"
  type        = string
  default     = "scope-b-tf-state-605134452604"
}

variable "lock_table_name" {
  type    = string
  default = "terraform-locks-b"
}

variable "github_org" {
  type = string
  default = "digambarrajaram"
}

variable "github_repo" {
  type = string
  default = "github_repo=AWS-Terraform-Drift-Reconciler"
}

variable "scan_allowed_branch" {
  description = "Branch allowed to assume the SCAN role (unattended, runs on every trigger)"
  type        = string
  default     = "main"
}

variable "apply_environment_name" {
  description = "GitHub Environment name (with required reviewers configured) that gates the APPLY role"
  type        = string
  default     = "scope-b-apply" # override per account, e.g. "prod-b-apply"
}

variable "managed_resource_prefix" {
  description = "Naming prefix used to scope S3/DynamoDB write permissions for the apply role to only resources this project manages (not the whole account)"
  type        = string
  default = "scope-b-"
}

