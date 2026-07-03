# Application Load Balancer Module - Outputs

# ALB Identifiers
output "alb_id" {
  description = "ID of the Application Load Balancer"
  value       = aws_lb.main.id
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer"
  value       = aws_lb.main.arn
}

output "alb_arn_suffix" {
  description = "ARN suffix of the ALB (for CloudWatch metrics)"
  value       = aws_lb.main.arn_suffix
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "Route 53 zone ID of the ALB"
  value       = aws_lb.main.zone_id
}

# Target Group
output "target_group_arn" {
  description = "ARN of the target group"
  value       = aws_lb_target_group.app.arn
}

output "target_group_arn_suffix" {
  description = "ARN suffix of the target group (for CloudWatch metrics)"
  value       = aws_lb_target_group.app.arn_suffix
}

output "target_group_name" {
  description = "Name of the target group"
  value       = aws_lb_target_group.app.name
}

# Listeners
output "https_listener_arn" {
  description = "ARN of the HTTPS listener (if certificate provided)"
  value       = var.certificate_arn != "" ? aws_lb_listener.https[0].arn : null
}

output "http_listener_arn" {
  description = "ARN of the HTTP listener"
  value       = aws_lb_listener.http.arn
}

# CloudWatch Alarms
output "response_time_alarm_arn" {
  description = "ARN of the response time CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.alb_target_response_time[0].arn : null
}

output "unhealthy_hosts_alarm_arn" {
  description = "ARN of the unhealthy hosts CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.alb_unhealthy_hosts[0].arn : null
}
