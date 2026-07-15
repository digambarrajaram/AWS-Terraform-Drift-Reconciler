
data "aws_caller_identity" "aws" {}

# Terraform backend configuration for EC2 module
# Uses S3 bucket and DynamoDB table created by bootstrap module
terraform {
  backend "s3" {
    bucket         = "scope-b-tf-state-605134452604"
    key            = "ec2_scope_b/terraform.tfstate"
    region         = "us-west-2"
    encrypt        = true
    dynamodb_table = "terraform-locks-b"
  }
}
