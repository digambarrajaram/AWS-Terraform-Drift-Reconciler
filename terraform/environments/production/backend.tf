
data "aws_caller_identity" "aws" {}


resource "aws_s3_bucket" "s3_bucket" {
  bucket        = "${var.resource_prefix}-aws-net-arch-state-${data.aws_caller_identity.aws.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "version" {
  bucket = "${var.resource_prefix}-aws-net-arch-state-${data.aws_caller_identity.aws.account_id}"
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_dynamodb_table" "dynamodb_table" {
  name     = "${var.resource_prefix}-aws-net-arch-locks-${data.aws_caller_identity.aws.account_id}"
  hash_key = "LockID"
  attribute {
    name = "LockID"
    type = "S"
  }
  billing_mode = "PAY_PER_REQUEST"

}
