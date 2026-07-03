# RDS Module - Outputs

# RDS Instance Identifiers
output "db_instance_id" {
  description = "ID of the RDS instance"
  value       = aws_db_instance.main.id
}

output "db_instance_arn" {
  description = "ARN of the RDS instance"
  value       = aws_db_instance.main.arn
}

output "db_instance_resource_id" {
  description = "Resource ID of the RDS instance"
  value       = aws_db_instance.main.resource_id
}

# Connection Information
output "db_endpoint" {
  description = "RDS instance endpoint (host:port)"
  value       = aws_db_instance.main.endpoint
}

output "db_address" {
  description = "RDS instance hostname"
  value       = aws_db_instance.main.address
}

output "db_port" {
  description = "RDS instance port"
  value       = aws_db_instance.main.port
}

output "db_name" {
  description = "Name of the initial database"
  value       = aws_db_instance.main.db_name
}

output "db_username" {
  description = "Master username for the database"
  value       = var.master_username
  sensitive   = true
}

# Secrets Manager
output "credentials_secret_arn" {
  description = "ARN of Secrets Manager secret containing RDS credentials"
  value       = aws_secretsmanager_secret.rds_credentials.arn
}

output "credentials_secret_name" {
  description = "Name of Secrets Manager secret containing RDS credentials"
  value       = aws_secretsmanager_secret.rds_credentials.name
}

# Subnet Group
output "db_subnet_group_name" {
  description = "Name of the DB subnet group"
  value       = aws_db_subnet_group.main.name
}

output "db_subnet_group_arn" {
  description = "ARN of the DB subnet group"
  value       = aws_db_subnet_group.main.arn
}

# Instance Details
output "db_engine" {
  description = "Database engine"
  value       = aws_db_instance.main.engine
}

output "db_engine_version" {
  description = "Database engine version"
  value       = aws_db_instance.main.engine_version_actual
}

output "db_instance_class" {
  description = "RDS instance class"
  value       = aws_db_instance.main.instance_class
}

output "db_multi_az" {
  description = "Whether the RDS instance is Multi-AZ"
  value       = aws_db_instance.main.multi_az
}

output "db_availability_zone" {
  description = "Availability zone of the RDS instance (primary for Multi-AZ)"
  value       = aws_db_instance.main.availability_zone
}

# CloudWatch Alarms
output "cpu_alarm_arn" {
  description = "ARN of the CPU utilization CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.database_cpu[0].arn : null
}

output "storage_alarm_arn" {
  description = "ARN of the storage CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.database_storage[0].arn : null
}

output "memory_alarm_arn" {
  description = "ARN of the memory CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.database_memory[0].arn : null
}

output "connections_alarm_arn" {
  description = "ARN of the connections CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.database_connections[0].arn : null
}
