output "ec2_security_group_id" {
  description = "Security group ID for EC2 instances"
  value       = aws_security_group.ec2.id
}

output "ec2_arn" {
  description = "ARN of the EC2 security group"
  value       = aws_security_group.ec2.arn
}

output "ec2_security_group_name" {
  description = "Name of the EC2 security group"
  value       = aws_security_group.ec2.name
}

output "ec2_security_group_description" {
  description = "Description of the EC2 security group"
  value       = aws_security_group.ec2.description
}

output "public_ip_address" {
  description = "Public IP address of the EC2 instance"
  value       = aws_instance.demo_server.public_ip
}

output "ec2_instance_id" {
  description = "ID of the EC2 instance"
  value       = aws_instance.demo_server.id
}

output "ami_id_used" {
  description = "AMI ID used for the EC2 instance"
  value       = aws_instance.demo_server.ami
}