# Compute Module - Outputs

# Auto Scaling Group
output "autoscaling_group_id" {
  description = "ID of the Auto Scaling Group"
  value       = aws_autoscaling_group.app.id
}

output "autoscaling_group_name" {
  description = "Name of the Auto Scaling Group"
  value       = aws_autoscaling_group.app.name
}

output "autoscaling_group_arn" {
  description = "ARN of the Auto Scaling Group"
  value       = aws_autoscaling_group.app.arn
}

# Launch Template
output "launch_template_id" {
  description = "ID of the launch template"
  value       = aws_launch_template.app.id
}

output "launch_template_arn" {
  description = "ARN of the launch template"
  value       = aws_launch_template.app.arn
}

output "launch_template_latest_version" {
  description = "Latest version of the launch template"
  value       = aws_launch_template.app.latest_version
}

# IAM Resources
output "iam_role_id" {
  description = "ID of the EC2 IAM role"
  value       = aws_iam_role.ec2.id
}

output "iam_role_arn" {
  description = "ARN of the EC2 IAM role"
  value       = aws_iam_role.ec2.arn
}

output "iam_role_name" {
  description = "Name of the EC2 IAM role"
  value       = aws_iam_role.ec2.name
}

output "iam_instance_profile_arn" {
  description = "ARN of the IAM instance profile"
  value       = aws_iam_instance_profile.ec2.arn
}

output "iam_instance_profile_name" {
  description = "Name of the IAM instance profile"
  value       = aws_iam_instance_profile.ec2.name
}

# Auto Scaling Policies
output "cpu_scaling_policy_arn" {
  description = "ARN of the CPU-based scaling policy"
  value       = aws_autoscaling_policy.cpu_target.arn
}

output "request_count_scaling_policy_arn" {
  description = "ARN of the request count-based scaling policy"
  value       = aws_autoscaling_policy.alb_request_count.arn
}

# CloudWatch Alarms
output "high_cpu_alarm_arn" {
  description = "ARN of the high CPU CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.asg_high_cpu[0].arn : null
}

output "low_healthy_hosts_alarm_arn" {
  description = "ARN of the low healthy hosts CloudWatch alarm (if enabled)"
  value       = var.enable_cloudwatch_alarms ? aws_cloudwatch_metric_alarm.asg_low_healthy_hosts[0].arn : null
}
