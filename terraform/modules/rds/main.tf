# RDS Module - Main Configuration

terraform {
  required_version = ">= 1.6.0"
}

# DB Subnet Group (spans all private DB subnets across AZs)
resource "aws_db_subnet_group" "main" {
  name       = lower("${var.resource_prefix}-${var.account_id}-${var.environment}-db-subnet-group")
  subnet_ids = var.subnet_ids

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-db-subnet-group"
      Environment = var.environment
      Purpose     = "RDS subnet group for Multi-AZ deployment"
    },
    var.tags
  )
}

# Random password for RDS master user
resource "random_password" "master" {
  length  = 32
  special = true
  # Exclude problematic characters for database passwords
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# AWS Secrets Manager secret for RDS credentials
resource "aws_secretsmanager_secret" "rds_credentials" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-credentials-"
  description             = "RDS master user credentials for ${var.environment}"
  recovery_window_in_days = var.secret_recovery_days
  kms_key_id              = "arn:aws:kms:region:account-id:key/key-id" # TODO: Replace with the actual ARN of the customer managed KMS key

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-credentials"
      Environment = var.environment
      Purpose     = "RDS database credentials"
    },
    var.tags
  )
}

resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    username = var.master_username
    password = random_password.master.result
    engine   = "mysql"
    host     = aws_db_instance.main.address
    port     = aws_db_instance.main.port
    dbname   = var.database_name
  })
}

# RDS Instance (Multi-AZ MySQL)
resource "aws_db_instance" "main" {
  identifier     = lower("${var.resource_prefix}-${var.environment}-mysql")
  engine         = "mysql"
  engine_version = var.engine_version
  instance_class = var.instance_class

  # Storage Configuration
  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = var.kms_key_id

  # Database Configuration
  db_name  = var.database_name
  username = var.master_username
  password = random_password.master.result
  port     = 3306

  # Network Configuration
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = false

  # High Availability
  multi_az               = var.multi_az
  availability_zone      = var.multi_az? null : var.preferred_az

  # Backup Configuration
  backup_retention_period   = var.backup_retention_days
  backup_window             = var.backup_window
  maintenance_window        = var.maintenance_window
  copy_tags_to_snapshot     = true
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.environment}-mysql-final-snapshot-${formatdate("YYYY-MM-DD-hhmm", timestamp())}"

  # Performance and Monitoring
  enabled_cloudwatch_logs_exports = ["error", "general", "slowquery"]
  monitoring_interval             = var.enable_enhanced_monitoring? 60 : 0
  monitoring_role_arn             = var.enable_enhanced_monitoring? var.monitoring_role_arn : null
  performance_insights_enabled    = var.enable_performance_insights
  performance_insights_retention_period = var.enable_performance_insights? 7 : null
  performance_insights_kms_key_id = var.performance_insights_kms_key_id # TODO: Confirm the KMS key ARN for Performance Insights

  # Parameter and Option Groups
  parameter_group_name = var.parameter_group_name!= ""? var.parameter_group_name : "default.mysql8.0"
  option_group_name    = var.option_group_name!= ""? var.option_group_name : "default:mysql-8-0"

  # Upgrade and Deletion Protection
  auto_minor_version_upgrade = var.auto_minor_version_upgrade
  deletion_protection        = var.deletion_protection

  # IAM Database Authentication
  iam_database_authentication_enabled = true

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-mysql-db"
      Environment = var.environment
      Purpose     = "Primary MySQL database"
      Engine      = "mysql"
      Version     = var.engine_version
    },
    var.tags
  )

  lifecycle {
    ignore_changes = [
      password,  # Password managed by Secrets Manager
      final_snapshot_identifier  # Timestamp changes each run
    ]
  }
}

# CloudWatch Alarms for RDS
resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "This metric monitors RDS CPU utilization"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-cpu-alarm"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "database_storage" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-low-storage"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 10737418240  # 10 GB in bytes
  alarm_description   = "This metric monitors RDS free storage space"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-storage-alarm"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "database_memory" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-low-memory"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "FreeableMemory"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 268435456  # 256 MB in bytes
  alarm_description   = "This metric monitors RDS freeable memory"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-memory-alarm"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "database_connections" {
  count               = var.enable_cloudwatch_alarms? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-high-connections"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = var.max_connections_threshold
  alarm_description   = "This metric monitors RDS database connections"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-rds-connections-alarm"
    Environment = var.environment
  }
}

