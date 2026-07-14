# Compute Module - Main Configuration (EC2 Auto Scaling)

provider "aws" {
  region = var.aws_region
}

terraform {
  required_version = ">= 1.6.0"
}

# Data source to find the latest Amazon Linux 2 AMI
data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# 3. Provision the EC2 Instance
resource "aws_instance" "demo_server" {
  ami           = var.ami_id!= ""? var.ami_id : data.aws_ami.amazon_linux_2.id
  instance_type = var.instance_type

  # Optional but highly recommended basic settings:
  vpc_security_group_ids = [aws_security_group.ec2.id]
  
  tags = {
    Name        = "PagerDuty-Demo-Host"
    Environment = "Development"
  }

  metadata_options {
    http_tokens = "required"
  }

  # Encryption for block devices
  root_block_device {
    encrypted = true
  }

  ebs_block_device {
    device_name = "/dev/sdm"
    volume_size = 100
    volume_type = "gp2"
    encrypted   = true
  }
}

resource "aws_security_group" "ec2" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-sg-"
  description = "Security group for EC2 application instances"
  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-sg"
      Environment = var.environment
      Purpose     = "Application server traffic control"
    },
    var.tags
  )

  lifecycle {
    create_before_destroy = true
  }
}

# EC2 Ingress Rule - From Bastion (optional)
resource "aws_vpc_security_group_ingress_rule" "ec2_from_bastion" {
  security_group_id            = aws_security_group.ec2.id
  description                  = "Allow SSH from bastion host"
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 22
  to_port                      = 22
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-from-bastion-ssh"
  }
}


# EC2 Egress Rule - Internet Access (for updates, API calls)
resource "aws_vpc_security_group_egress_rule" "ec2_https_internet" {
  security_group_id = aws_security_group.ec2.id
  description       = "Allow HTTPS to internet (package updates, API calls)"
  cidr_ipv4         = "10.0.0.0/8" # TODO: Replace with the actual required CIDR range
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-https-egress"
  }
}

resource "aws_vpc_security_group_egress_rule" "ec2_http_internet" {
  security_group_id = aws_security_group.ec2.id
  description       = "Allow HTTP to internet (package repositories)"
  cidr_ipv4         = "10.0.0.0/8" # TODO: Replace with the actual required CIDR range
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-http-egress"
  }
}

# ── DynamoDB: Audit Trail & Drift State ─────────────────────────────
# Single-table design for append-only audit records, timeline events,
# PR history, and system configuration.

resource "aws_dynamodb_table" "drift_audit" {
  name         = "${var.resource_prefix}-${var.environment}-drift-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  # GSI for querying by resource ID across record types
  global_secondary_index {
    name            = "resource-index"
    projection_type = "ALL"

    key_schema {
      attribute_name = "resource_id"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "timestamp"
      key_type       = "RANGE"
    }
  }

  attribute {
    name = "resource_id"
    type = "S"
  }
  attribute {
    name = "timestamp"
    type = "S"
  }

  # GSI for querying by action type (scan, pr_created, pr_merged, etc.)
  global_secondary_index {
    name            = "action-index"
    projection_type = "ALL"

    key_schema {
      attribute_name = "action"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "timestamp"
      key_type       = "RANGE"
    }
  }

  attribute {
    name = "action"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
    kms_key_arn = "arn:aws:kms:region:account-id:key/key-id" # TODO: Replace with the actual KMS key ARN
  }

  tags = merge(
    var.tags,
    {
      Name        = "${var.resource_prefix}-${var.environment}-drift-audit"
      Environment = var.environment
      Purpose     = "Drift reconciler audit trail and state persistence"
    }
  )
}

# ── IAM Policy for Drift Reconciler to access DynamoDB ──────────────
resource "aws_iam_policy" "drift_reconciler_dynamodb" {
  name        = "${var.resource_prefix}-${var.environment}-drift-dynamodb-policy"
  description = "Allow Drift Reconciler to read/write audit trail and state in DynamoDB"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:BatchWriteItem",
        ]
        Resource = [
          aws_dynamodb_table.drift_audit.arn,
          "${aws_dynamodb_table.drift_audit.arn}/index/*",
        ]
      }
    ]
  })
}

output "audit_table_name" {
  description = "DynamoDB table for drift audit trail"
  value       = aws_dynamodb_table.drift_audit.name
}

output "audit_table_arn" {
  description = "ARN of DynamoDB audit table"
  value       = aws_dynamodb_table.drift_audit.arn
}