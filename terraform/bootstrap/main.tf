provider "aws" {
  region = var.aws_region
}


data "aws_caller_identity" "aws" {}

# State Bucket
resource "aws_s3_bucket" "terraform_state" {
  bucket        = "${var.project_name}-state-${data.aws_caller_identity.aws.account_id}"
  force_destroy = true
  lifecycle {
    prevent_destroy = false
  }

  tags = {
    Name      = "Terraform State Storage"
    ManagedBy = "Terraform-Bootstrap"
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket                  = aws_s3_bucket.terraform_state.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

# State Lock Table
resource "aws_dynamodb_table" "terraform_locks" {
  name         = "${var.project_name}-locks-${data.aws_caller_identity.aws.account_id}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name      = "Terraform State Lock Table"
    ManagedBy = "Terraform-Bootstrap"
  }
}
