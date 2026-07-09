# Quick Start Guide

This guide provides step-by-step instructions to deploy the AWS Networking Architecture using Terraform.

## Prerequisites

- [Terraform](https://www.terraform.io/downloads.html) (v1.6.0+)
- [AWS CLI](https://aws.amazon.com/cli/) (v2.0+)
- Configured AWS credentials (`aws configure`)

## deployment Steps

### 1. Bootstrap the Infrastructure
Initialize the Terraform state backend (S3 Bucket and DynamoDB Table).

```bash
cd terraform/bootstrap
terraform init
terraform apply -auto-approve
```

Note the `dynamodb_table_name` and `s3_bucket_name` outputs.

### 2. Configure Backend
Ensure `terraform/environments/production/backend.tf` matches the outputs from the bootstrap step.

### 3. Deploy Production Environment
Deploy the network and application infrastructure.

```bash
cd ../environments/production
terraform init
terraform apply
```

Review the plan and type `yes` to confirm.

## Validation

After deployment, note the following outputs:
- `alb_dns_name`: The URL of your application load balancer.
- `rds_endpoint`: Database connection endpoint.
- `vpc_id`: The ID of the created VPC.

Access the application via the ALB DNS name (e.g., `http://digi-production-app-alb-....elb.amazonaws.com`).

## Cleanup

To destroy the infrastructure:

1. Destroy Production resources:
   ```bash
   cd terraform/environments/production
   terraform destroy
   ```

2. Destroy Bootstrap resources:
   ```bash
   cd ../../bootstrap
   terraform destroy
   ```
