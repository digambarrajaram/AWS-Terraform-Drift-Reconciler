
data "aws_caller_identity" "aws" {}

# Terraform backend configuration for EC2 module
# Uses S3 bucket and DynamoDB table created by bootstrap module
terraform {
  backend "s3" {
    bucket   = "aws-terraform-drift-reconciler-state-605134452604"
    key      = "ec2/terraform.tfstate"
    region   = "us-east-1"
    encrypt  = true
  }
}
