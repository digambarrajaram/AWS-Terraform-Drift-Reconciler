# Production Environment - Main Configuration

# Data source for current AWS identity
data "aws_caller_identity" "current" {}

# Data source for availability zones
data "aws_availability_zones" "available" {
  state = "available"
}

# Data source for latest Amazon Linux 2023 AMI
data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Local variables
locals {
  availability_zones = slice(data.aws_availability_zones.available.names, 0, 3)

  common_tags = {
    Architecture = "Multi-tier web application"
    Compliance   = "PCI-DSS-GDPR-aligned"
  }
}

# IAM Role and Log Group for VPC Flow Logs
resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/aws/vpc/${var.resource_prefix}-${var.environment}-flow-logs"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_iam_role" "vpc_flow_logs" {
  name = "${var.resource_prefix}-${var.environment}-vpc-flow-logs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "vpc-flow-logs.amazonaws.com"
        }
      }
    ]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "vpc_flow_logs" {
  name = "${var.resource_prefix}-${var.environment}-vpc-flow-logs-policy"
  role = aws_iam_role.vpc_flow_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Effect = "Allow"
        Resource = "*"
      }
    ]
  })
}

# VPC Module
module "vpc" {
  source = "../../modules/vpc"

  resource_prefix    = var.resource_prefix
  account_id         = data.aws_caller_identity.current.account_id
  environment        = var.environment
  vpc_cidr           = var.vpc_cidr
  availability_zones = local.availability_zones

  enable_flow_logs         = true
  flow_log_role_arn        = aws_iam_role.vpc_flow_logs.arn
  flow_log_destination_arn = aws_cloudwatch_log_group.vpc_flow_logs.arn

  tags = local.common_tags
}

# NAT Gateway Module
module "nat_gateway" {
  source = "../../modules/nat-gateway"

  resource_prefix        = var.resource_prefix
  account_id             = data.aws_caller_identity.current.account_id
  environment            = var.environment
  vpc_id                 = module.vpc.vpc_id
  public_subnet_ids      = module.vpc.public_subnet_ids
  private_app_subnet_ids = module.vpc.private_app_subnet_ids
  availability_zones     = local.availability_zones
  internet_gateway_id    = module.vpc.internet_gateway_id

  tags = local.common_tags

  depends_on = [module.vpc]
}

# Security Groups Module
module "security_groups" {
  source = "../../modules/security-groups"

  resource_prefix      = var.resource_prefix
  account_id           = data.aws_caller_identity.current.account_id
  environment          = var.environment
  vpc_id               = module.vpc.vpc_id
  app_port             = var.app_port
  enable_bastion       = var.enable_bastion
  bastion_allowed_cidr = var.bastion_allowed_cidr

  tags = local.common_tags

  depends_on = [module.vpc]
}

# Application Load Balancer Module
module "alb" {
  source = "../../modules/alb"

  resource_prefix            = var.resource_prefix
  account_id                 = data.aws_caller_identity.current.account_id
  environment                = var.environment
  vpc_id                     = module.vpc.vpc_id
  public_subnet_ids          = module.vpc.public_subnet_ids
  security_group_id          = module.security_groups.alb_security_group_id
  app_port                   = var.app_port
  health_check_path          = var.health_check_path
  certificate_arn            = var.acm_certificate_arn
  enable_sticky_sessions     = true
  enable_deletion_protection = var.enable_alb_deletion_protection
  enable_cloudwatch_alarms   = true

  tags = local.common_tags

  depends_on = [module.vpc, module.security_groups]
}

# IAM Role for RDS Enhanced Monitoring
resource "aws_iam_role" "rds_monitoring" {
  name = "${var.resource_prefix}-${var.environment}-rds-monitoring-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "monitoring.rds.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# RDS Module
module "rds" {
  source = "../../modules/rds"

  resource_prefix   = var.resource_prefix
  account_id        = data.aws_caller_identity.current.account_id
  environment       = var.environment
  subnet_ids        = module.vpc.private_db_subnet_ids
  security_group_id = module.security_groups.rds_security_group_id

  instance_class        = var.rds_instance_class
  engine_version        = var.rds_engine_version
  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = var.rds_max_allocated_storage
  database_name         = var.rds_database_name
  master_username       = var.rds_master_username

  multi_az              = true
  backup_retention_days = var.rds_backup_retention_days
  backup_window         = var.rds_backup_window
  maintenance_window    = var.rds_maintenance_window
  skip_final_snapshot   = false
  deletion_protection   = var.enable_rds_deletion_protection

  enable_enhanced_monitoring  = true
  monitoring_role_arn         = aws_iam_role.rds_monitoring.arn
  enable_performance_insights = true
  enable_cloudwatch_alarms    = true

  tags = local.common_tags

  depends_on = [module.vpc, module.security_groups]
}

# ElastiCache Module
module "elasticache" {
  source = "../../modules/elasticache"

  resource_prefix   = var.resource_prefix
  account_id        = data.aws_caller_identity.current.account_id
  environment       = var.environment
  subnet_ids        = module.vpc.private_db_subnet_ids
  security_group_id = module.security_groups.elasticache_security_group_id

  node_type          = var.redis_node_type
  engine_version     = var.redis_engine_version
  num_shards         = var.redis_num_shards
  replicas_per_shard = var.redis_replicas_per_shard

  automatic_failover_enabled = true
  multi_az_enabled           = true
  transit_encryption_enabled = true
  auth_token_enabled         = true

  snapshot_retention_days = var.redis_snapshot_retention_days
  maintenance_window      = var.redis_maintenance_window
  snapshot_window         = var.redis_snapshot_window

  enable_cloudwatch_alarms = true
  slow_log_group_name      = var.redis_slow_log_group
  engine_log_group_name    = var.redis_engine_log_group

  tags = local.common_tags

  depends_on = [module.vpc, module.security_groups]
}

# Compute Module (Auto Scaling)
module "compute" {
  source = "../../modules/compute"

  resource_prefix   = var.resource_prefix
  account_id        = data.aws_caller_identity.current.account_id
  environment       = var.environment
  subnet_ids        = module.vpc.private_app_subnet_ids
  security_group_id = module.security_groups.ec2_security_group_id
  target_group_arn  = module.alb.target_group_arn

  ami_id                     = data.aws_ami.amazon_linux_2023.id
  instance_type              = var.ec2_instance_type
  root_volume_size           = var.ec2_root_volume_size
  enable_detailed_monitoring = true

  min_size         = var.asg_min_size
  max_size         = var.asg_max_size
  desired_capacity = var.asg_desired_capacity

  cpu_target_value           = var.asg_cpu_target
  request_count_target_value = var.asg_request_count_target
  alb_target_group_label     = "${module.alb.alb_arn_suffix}/${module.alb.target_group_arn_suffix}"

  app_port             = var.app_port
  cloudwatch_log_group = var.cloudwatch_log_group
  rds_secret_arn       = module.rds.credentials_secret_arn
  redis_secret_arn     = module.elasticache.auth_secret_arn

  secrets_arns = [
    module.rds.credentials_secret_arn,
    module.elasticache.auth_secret_arn
  ]

  enable_cloudwatch_alarms = true

  tags = local.common_tags

  depends_on = [
    module.vpc,
    module.nat_gateway,
    module.security_groups,
    module.alb,
    module.rds,
    module.elasticache
  ]
}

# CloudWatch Log Group for application logs
resource "aws_cloudwatch_log_group" "app" {
  name              = var.cloudwatch_log_group
  retention_in_days = var.log_retention_days

  tags = merge(
    {
      Name        = var.cloudwatch_log_group
      Environment = var.environment
      Purpose     = "Application and infrastructure logs"
    },
    local.common_tags
  )
}

# CloudWatch Log Groups for ElastiCache
resource "aws_cloudwatch_log_group" "redis_slow_log" {
  name              = var.redis_slow_log_group
  retention_in_days = var.log_retention_days

  tags = merge(
    {
      Name        = var.redis_slow_log_group
      Environment = var.environment
      Purpose     = "ElastiCache slow logs"
    },
    local.common_tags
  )
}

resource "aws_cloudwatch_log_group" "redis_engine_log" {
  name              = var.redis_engine_log_group
  retention_in_days = var.log_retention_days

  tags = merge(
    {
      Name        = var.redis_engine_log_group
      Environment = var.environment
      Purpose     = "ElastiCache engine logs"
    },
    local.common_tags
  )
}
