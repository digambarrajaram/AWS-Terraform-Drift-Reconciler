# ElastiCache Module - Main Configuration

terraform {
  required_version = ">= 1.6.0"
}

# ElastiCache Subnet Group (spans all private DB subnets across AZs)
resource "aws_elasticache_subnet_group" "main" {
  name   = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-subnet-group"
  subnet_ids = var.subnet_ids

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-subnet-group"
      Environment = var.environment
      Purpose     = "ElastiCache subnet group for Redis cluster"
    },
    var.tags
  )
}

# ElastiCache Parameter Group for Redis
resource "aws_elasticache_parameter_group" "main" {
  name   = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-params"
  family = "redis7"

  # Redis configuration parameters
  parameter {
    name  = "cluster-enabled"
    value = "yes"
  }

  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru"
  }

  parameter {
    name  = "timeout"
    value = "300"
  }

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-params"
      Environment = var.environment
      Purpose     = "Redis parameter group"
    },
    var.tags
  )
}

# ElastiCache Replication Group (Redis Cluster Mode)
resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = lower("${var.resource_prefix}-${var.environment}-redis")
  description                = "Redis cluster for ${var.environment} environment"
  engine                     = "redis"
  engine_version             = var.engine_version
  node_type                  = var.node_type
  port                       = 6379

  # Cluster Configuration
  num_node_groups         = var.num_shards
  replicas_per_node_group = var.replicas_per_shard
  
  # Network Configuration
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [var.security_group_id]
  
  # Parameter Group
  parameter_group_name = aws_elasticache_parameter_group.main.name

  # High Availability
  automatic_failover_enabled = var.automatic_failover_enabled
  multi_az_enabled           = var.multi_az_enabled

  # Encryption
  at_rest_encryption_enabled = true
  transit_encryption_enabled = var.transit_encryption_enabled
  auth_token                 = var.transit_encryption_enabled && var.auth_token_enabled ? random_password.redis_auth[0].result : null
  kms_key_id                 = var.kms_key_id

  # Maintenance and Backup
  maintenance_window       = var.maintenance_window
  snapshot_window          = var.snapshot_window
  snapshot_retention_limit = var.snapshot_retention_days
  
  # Auto Upgrades
  auto_minor_version_upgrade = var.auto_minor_version_upgrade
  
  # Notifications
  notification_topic_arn = var.sns_topic_arn

  # Logging
  log_delivery_configuration {
    destination      = var.slow_log_group_name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "slow-log"
  }

  log_delivery_configuration {
    destination      = var.engine_log_group_name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "engine-log"
  }

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-cluster"
      Environment = var.environment
      Purpose     = "Application caching and session storage"
      Engine      = "redis"
      Version     = var.engine_version
    },
    var.tags
  )

  lifecycle {
    ignore_changes = [
      auth_token  # Auth token managed separately if enabled
    ]
  }
}

# Random password for Redis AUTH (if enabled)
resource "random_password" "redis_auth" {
  count   = var.auth_token_enabled ? 1 : 0
  length  = 64
  special = false  # Redis AUTH token has character restrictions
}

# AWS Secrets Manager secret for Redis AUTH token
resource "aws_secretsmanager_secret" "redis_auth" {
  count                   = var.auth_token_enabled ? 1 : 0
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-auth-"
  description             = "Redis AUTH token for ${var.environment}"
  recovery_window_in_days = var.secret_recovery_days

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-auth"
      Environment = var.environment
      Purpose     = "Redis authentication token"
    },
    var.tags
  )
}

resource "aws_secretsmanager_secret_version" "redis_auth" {
  count     = var.auth_token_enabled ? 1 : 0
  secret_id = aws_secretsmanager_secret.redis_auth[0].id
  secret_string = jsonencode({
    auth_token = random_password.redis_auth[0].result
    endpoint   = aws_elasticache_replication_group.main.configuration_endpoint_address
    port       = 6379
  })
}

# CloudWatch Alarms for ElastiCache
resource "aws_cloudwatch_metric_alarm" "cache_cpu" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 75
  alarm_description   = "This metric monitors ElastiCache CPU utilization"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.main.id
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-cpu-alarm"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "cache_memory" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-high-memory"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 90
  alarm_description   = "This metric monitors ElastiCache memory usage"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.main.id
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-memory-alarm"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "cache_evictions" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-high-evictions"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Evictions"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 1000
  alarm_description   = "This metric monitors ElastiCache evictions"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.main.id
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-evictions-alarm"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "cache_replication_lag" {
  count               = var.enable_cloudwatch_alarms && var.replicas_per_shard > 0 ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-replication-lag"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ReplicationLag"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 30  # 30 seconds
  alarm_description   = "This metric monitors ElastiCache replication lag"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.main.id
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-redis-replication-lag-alarm"
    Environment = var.environment
  }
}
