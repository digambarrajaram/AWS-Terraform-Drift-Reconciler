output "state_bucket_name" {
  value       = aws_s3_bucket.terraform_state.bucket
  description = "The name of the S3 bucket created for Terraform state"
}

output "dynamodb_table_name" {
  value       = aws_dynamodb_table.terraform_locks.name
  description = "The name of the DynamoDB table created for state locking"
}

output "region" {
  value       = var.aws_region
  description = "The AWS region where resources were created"
}

output "bucket_id" {
  value       = aws_s3_bucket.terraform_state.id
  description = "The ID of the S3 bucket created for Terraform state"
}

output "dynamodb_table_id" {
  value       = aws_dynamodb_table.terraform_locks.id
  description = "The ID of the DynamoDB table created for state locking"
}
