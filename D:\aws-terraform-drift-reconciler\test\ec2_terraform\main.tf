terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# ─────────────────────────────────────────────
# STANDARD DATA SOURCES
# ─────────────────────────────────────────────

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_vpc" "default" {
  default = true
}

# Ubuntu 24.04 LTS (Noble) - Free Tier Eligible, 8GB root volume
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

# ─────────────────────────────────────────────
# VARIABLES
# ─────────────────────────────────────────────

variable "allowed_ssh_cidr" {
  description = "Your IP for SSH. Get it: curl https://checkip.amazonaws.com"
  type        = string
  default     = "203.0.113.10/32" # <-- CHANGE THIS!
}

variable "key_name" {
  description = "AWS EC2 Key Pair name"
  type        = string
  default     = "drrift-key"
}

# ─────────────────────────────────────────────
# SECURITY GROUP
# ─────────────────────────────────────────────

resource "aws_security_group" "drift_web_ssh_sg" {
  name        = "web-ssh-security-group"
  description = "Allow restricted SSH and public HTTPS"
  vpc_id      = data.aws_vpc.default.id

  tags = {
    Name = "drift-web-ssh-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "ssh_ingress" {
  security_group_id = aws_security_group.drift_web_ssh_sg.id
  from_port         = 2222
  to_port           = 2222
  ip_protocol       = "tcp"
  cidr_ipv4         = var.allowed_ssh_cidr
  description       = "SSH from admin IP only"
}

resource "aws_vpc_security_group_ingress_rule" "https_ingress" {
  security_group_id = aws_security_group.drift_web_ssh_sg.id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "HTTPS from internet"
}

resource "aws_vpc_security_group_egress_rule" "all_egress" {
  security_group_id = aws_security_group.drift_web_ssh_sg.id
  ip_protocol       = "-1" # All traffic
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Allow all outbound"
}

# ─────────────────────────────────────────────
# EC2 INSTANCE - FREE TIER (t2.micro)
# ─────────────────────────────────────────────

resource "aws_instance" "drift_web_server" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = "t2.nano" # FREE TIER ELIGIBLE (750 hrs/month)
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.drift_web_ssh_sg.id]

  # Ubuntu 24.04 uses 8GB by default - no override needed
  # But explicitly set to match and avoid surprises
  root_block_device {
    volume_size           = 8
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_tokens = "required" # IMDSv2
  }

  tags = { "Name" : "WebServer" }
}

# ─────────────────────────────────────────────
# OUTPUTS
# ─────────────────────────────────────────────

output "instance_public_ip" {
  value = aws_instance.drift_web_server.public_ip
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_instance.drift_web_server.public_ip}"
}