# ElastiCache Module - Outputs

# ElastiCache Cluster Identifiers
output "replication_group_id" {
  description = "ID of the ElastiCache replication group"
  value       = aws_elasticache_replication_group.main.id
}

output "replication_group_arn" {
  description = "ARN of the ElastiCache replication group"
  value       = aws_elasticache_replication_group.main.arn
}

# Connection Information
output "configuration_endpoint" {
  description = "Configuration endpoint for Redis cluster (cluster mode enabled)"
  value       = aws_elasticache_replication_group.main.configuration_endpoint_address
}

output "primary_endpoint" {
  description = "Primary endpoint for Redis (cluster mode disabled)"
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "reader_endpoint" {
  description = "Reader endpoint for Redis (read replicas)"
  value       = aws_elasticache_replication_group.main.reader_endpoint_address
}

output "port" {
  description = "Redis port"
  value       = 6379
}

# Secrets Manager
output "auth_secret_arn" {
  description = "ARN of Secrets Manager secret containing Redis AUTH token (if enabled)"
  value       = var.auth_token_enabled ? aws_secretsmanager_secret.redis_auth[0].arn : null
}

output "auth_secret_name" {
  description = "Name of Secrets Manager secret containing Redis AUTH token (if enabled)"
  value       = var.auth_token_enabled ? aws_secretsmanager_secret.redis_auth[0].name : null
}

# Subnet Group
output "subnet_group_name" {
  description = "Name of the ElastiCache subnet group"
  value       = aws_elasticache_subnet_group.main.name
}

# Parameter Group
output "parameter_group_name" {
  description = "Name of the ElastiCache parameter group"
  value       = aws_elasticache_parameter_group.main.name
}

# Cluster Details
output "engine" {
  description = "Cache engine (redis)"
  value       = "redis"
}

output "engine_version" {
  description = "Redis engine version"
  value       = var.engine_version
}

output "node_type" {
  description = "Node type for cache nodes"
  value       = var.node_type
}

output "num_shards" {
  description = "Number of shards in the cluster"
  value       = var.num_shards
}

output "replicas_per_shard" {
  description = "Number of replicas per shard"
  value       = var.replicas_per_shard
}

output "multi_az_enabled" {
  description = "Whether Multi-AZ is enabled"
  value       = var.multi_az_enabled
}

# CloudWatch Alarms
output "cpu_alarm_arn" {
  description = "ARN of the CPU utilization CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.cache_cpu[0].arn : null
}

output "memory_alarm_arn" {
  description = "ARN of the memory CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.cache_memory[0].arn : null
}

output "evictions_alarm_arn" {
  description = "ARN of the evictions CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.cache_evictions[0].arn : null
}

output "replication_lag_alarm_arn" {
  description = "ARN of the replication lag CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms && var.replicas_per_shard > 0 ? aws_cloudwatch_metric_alarm.cache_replication_lag[0].arn : null
}
