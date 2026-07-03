# Security Groups Module - Main Configuration

terraform {
  required_version = ">= 1.6.0"
}

# Security Group for Application Load Balancer
resource "aws_security_group" "alb" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-sg-"
  description = "Security group for Application Load Balancer"
  vpc_id      = var.vpc_id

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-sg"
      Environment = var.environment
      Purpose     = "ALB traffic control"
    },
    var.tags
  )

  lifecycle {
    create_before_destroy = true
  }
}

# ALB Ingress Rules
resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "Allow HTTP from internet"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-http-ingress"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "Allow HTTPS from internet"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-https-ingress"
  }
}

# ALB Egress Rule
resource "aws_vpc_security_group_egress_rule" "alb_to_ec2" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Allow ALB to communicate with EC2 instances"
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = var.app_port
  to_port                      = var.app_port
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-to-ec2-egress"
  }
}

# Security Group for EC2 Instances
resource "aws_security_group" "ec2" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-sg-"
  description = "Security group for EC2 application instances"
  vpc_id      = var.vpc_id

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

# EC2 Ingress Rule - From ALB
resource "aws_vpc_security_group_ingress_rule" "ec2_from_alb" {
  security_group_id            = aws_security_group.ec2.id
  description                  = "Allow traffic from ALB"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.app_port
  to_port                      = var.app_port
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-from-alb-ingress"
  }
}

# EC2 Ingress Rule - From Bastion (optional)
resource "aws_vpc_security_group_ingress_rule" "ec2_from_bastion" {
  count                        = var.enable_bastion ? 1 : 0
  security_group_id            = aws_security_group.ec2.id
  description                  = "Allow SSH from bastion host"
  referenced_security_group_id = aws_security_group.bastion[0].id
  from_port                    = 22
  to_port                      = 22
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-from-bastion-ssh"
  }
}

# EC2 Egress Rule - To RDS
resource "aws_vpc_security_group_egress_rule" "ec2_to_rds" {
  security_group_id            = aws_security_group.ec2.id
  description                  = "Allow EC2 to communicate with RDS"
  referenced_security_group_id = aws_security_group.rds.id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-to-rds-egress"
  }
}

# EC2 Egress Rule - To ElastiCache
resource "aws_vpc_security_group_egress_rule" "ec2_to_elasticache" {
  security_group_id            = aws_security_group.ec2.id
  description                  = "Allow EC2 to communicate with ElastiCache"
  referenced_security_group_id = aws_security_group.elasticache.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-to-elasticache-egress"
  }
}

# EC2 Egress Rule - Internet Access (for updates, API calls)
resource "aws_vpc_security_group_egress_rule" "ec2_https_internet" {
  security_group_id = aws_security_group.ec2.id
  description       = "Allow HTTPS to internet (package updates, API calls)"
  cidr_ipv4         = "0.0.0.0/0"
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
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-http-egress"
  }
}

# Security Group for RDS
resource "aws_security_group" "rds" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-sg-"
  description = "Security group for RDS database instances"
  vpc_id      = var.vpc_id

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-sg"
      Environment = var.environment
      Purpose     = "Database access control"
    },
    var.tags
  )

  lifecycle {
    create_before_destroy = true
  }
}

# RDS Ingress Rule - From EC2
resource "aws_vpc_security_group_ingress_rule" "rds_from_ec2" {
  security_group_id            = aws_security_group.rds.id
  description                  = "Allow MySQL access from EC2 instances"
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-from-ec2-ingress"
  }
}

# RDS Ingress Rule - From Bastion (optional, for troubleshooting)
resource "aws_vpc_security_group_ingress_rule" "rds_from_bastion" {
  count                        = var.enable_bastion ? 1 : 0
  security_group_id            = aws_security_group.rds.id
  description                  = "Allow MySQL access from bastion (troubleshooting only)"
  referenced_security_group_id = aws_security_group.bastion[0].id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-from-bastion-ingress"
  }
}

# Security Group for ElastiCache
resource "aws_security_group" "elasticache" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-elasticache-sg-"
  description = "Security group for ElastiCache Redis cluster"
  vpc_id      = var.vpc_id

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-elasticache-sg"
      Environment = var.environment
      Purpose     = "Cache access control"
    },
    var.tags
  )

  lifecycle {
    create_before_destroy = true
  }
}

# ElastiCache Ingress Rule - From EC2
resource "aws_vpc_security_group_ingress_rule" "elasticache_from_ec2" {
  security_group_id            = aws_security_group.elasticache.id
  description                  = "Allow Redis access from EC2 instances"
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-elasticache-from-ec2-ingress"
  }
}

# Security Group for Bastion Host (optional)
resource "aws_security_group" "bastion" {
  count       = var.enable_bastion ? 1 : 0
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-bastion-sg-"
  description = "Security group for bastion host (SSH access)"
  vpc_id      = var.vpc_id

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-bastion-sg"
      Environment = var.environment
      Purpose     = "Bastion host access control"
    },
    var.tags
  )

  lifecycle {
    create_before_destroy = true
  }
}

# Bastion Ingress Rule - SSH from specific CIDR
resource "aws_vpc_security_group_ingress_rule" "bastion_ssh" {
  count             = var.enable_bastion ? 1 : 0
  security_group_id = aws_security_group.bastion[0].id
  description       = "Allow SSH from authorized networks"
  cidr_ipv4         = var.bastion_allowed_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-bastion-ssh-ingress"
  }
}

# Bastion Egress Rule - To EC2 (SSH)
resource "aws_vpc_security_group_egress_rule" "bastion_to_ec2" {
  count                        = var.enable_bastion ? 1 : 0
  security_group_id            = aws_security_group.bastion[0].id
  description                  = "Allow SSH from bastion to EC2 instances"
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 22
  to_port                      = 22
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-bastion-to-ec2-egress"
  }
}

# Bastion Egress Rule - To RDS (troubleshooting)
resource "aws_vpc_security_group_egress_rule" "bastion_to_rds" {
  count                        = var.enable_bastion ? 1 : 0
  security_group_id            = aws_security_group.bastion[0].id
  description                  = "Allow MySQL access from bastion to RDS"
  referenced_security_group_id = aws_security_group.rds.id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-bastion-to-rds-egress"
  }
}
