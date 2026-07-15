# bootstrap/state-backend.tf
#
# Creates the S3 bucket + DynamoDB lock table for THIS account's tfstate.
# Uses local state itself (chicken-and-egg -- can't store state in a bucket
# that doesn't exist yet). Run from its own directory per account.

resource "aws_s3_bucket" "tf_state" {
  bucket = var.state_bucket_name

  force_destroy = true

  lifecycle {
    prevent_destroy = false
  }

  tags = {
    Purpose = "terraform-state"
    Account = var.account_label
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tf_lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Purpose = "terraform-state-locking"
    Account = var.account_label
  }
}
