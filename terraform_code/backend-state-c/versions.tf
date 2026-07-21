# bootstrap/versions.tf + provider.tf combined
# Shared across all bootstrap .tf files in this directory — only ONE of
# each block per directory, which is why this isn't duplicated per-file.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
