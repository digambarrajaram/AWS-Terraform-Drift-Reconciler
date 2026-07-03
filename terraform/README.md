# AWS Infrastructure via Terraform

This repository contains the Terraform Infrastructure as Code (IaC) to deploy a production-grade, highly available 3-tier web architecture on AWS.

## 🏗 Architecture Summary

*   **Network**: Multi-AZ VPC (3 AZs), Public/Private Subnet isolation, NAT Gateways (Outbound only).
*   **Compute**: EC2 Auto Scaling Group with strict security profiles (IMDSv2, private subnets).
*   **Database**: RDS MySQL (Multi-AZ) & ElastiCache Redis (Cluster mode enabled).
*   **Security**: Least-privilege Security Groups, IAM Roles, KMS Encryption (Storage/DB), and locked-down default SGs.
*   **Routing**: Application Load Balancer (ALB) public-facing entry point.

## 🚀 Deployment Guide

### 1. Prerequisites
*   Terraform `>= 1.6.0`
*   AWS CLI (configured with `aws configure`)

### 2. Boostrap Backend (One-time Setup)
We use a dedicated bootstrap process to provision the secure S3 Bucket and DynamoDB Lock Table for Terraform state.

```bash
# 1. Run the bootstrap automation
cd terraform/scripts
./bootstrap_infrastructure.sh
```
*Output: Automatically provisions S3/DynamoDB and configures `environments/production/backend.tf`.*

### 3. Deploy Infrastructure
```bash
# 1. Navigate to production environment
cd terraform/environments/production

# 2. Initialize
terraform init

# 3. Plan & Apply
terraform plan
terraform apply
```

## ⚙️ Configuration
Modify `terraform/environments/production/terraform.tfvars` to customize your deployment:
*   **Resource Naming**: `resource_prefix`, `project_name`
*   **Capacity**: `rds_instance_class`, `redis_node_type`
*   **Network**: `vpc_cidr`, `bastion_allowed_cidr`

## 📂 Structure
```
terraform/
├── bootstrap/             # State management infrastructure (S3/DynamoDB)
├── environments/
│   └── production/        # Main deployment (Variables, Main, Backend config)
├── modules/               # Reusable infrastructure components
│   ├── vpc                # Network topology & routing
│   ├── security-groups    # Firewall rules
│   ├── compute            # ASG & Launch Templates
│   ├── rds                # Relational Database
│   ├── elasticache        # Redis Cluster
│   ├── alb                # Load Balancer
│   └── nat-gateway        # Internet egress
└── scripts/               # Automation helper scripts
```

## ⚠️ Destruction
To tear down the environment:
```bash
cd terraform/environments/production
terraform destroy -auto-approve
```
*Note: The state bucket created by bootstrap has `prevent_destroy` enabled to protect your history.*
