# Compute Module - Main Configuration (EC2 Auto Scaling)

terraform {
  required_version = ">= 1.6.0"
}

# IAM Role for EC2 Instances
resource "aws_iam_role" "ec2" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-role-"
  description = "IAM role for EC2 instances"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-role"
      Environment = var.environment
      Purpose     = "EC2 instance role"
    },
    var.tags
  )
}

# Attach AWS Managed Policies
resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ec2_cloudwatch" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# Custom IAM Policy for Secrets Access
resource "aws_iam_policy" "ec2_secrets" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-secrets-policy-"
  description = "Allow EC2 to access RDS and Redis secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect = "Allow"
          Action = [
            "secretsmanager:GetSecretValue",
            "secretsmanager:DescribeSecret"
          ]
          Resource = var.secrets_arns
        }
      ],
      length(var.kms_key_arns) > 0 ? [
        {
          Effect = "Allow"
          Action = [
            "kms:Decrypt",
            "kms:DescribeKey"
          ]
          Resource = var.kms_key_arns
        }
      ] : []
    )
  })

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-secrets-policy"
      Environment = var.environment
    },
    var.tags
  )
}

resource "aws_iam_role_policy_attachment" "ec2_secrets" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.ec2_secrets.arn
}

# IAM Instance Profile
resource "aws_iam_instance_profile" "ec2" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-profile-"
  role        = aws_iam_role.ec2.name

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-ec2-profile"
      Environment = var.environment
    },
    var.tags
  )
}

# Launch Template for EC2 Instances
resource "aws_launch_template" "app" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-app-lt-"
  description   = "Launch template for application servers"
  image_id      = var.ami_id
  instance_type = var.instance_type
  
  vpc_security_group_ids = [var.security_group_id]

  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2.arn
  }

  # EBS Volume Configuration
  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = var.root_volume_size
      volume_type           = "gp3"
      iops                  = 3000
      throughput            = 125
      encrypted             = true
      delete_on_termination = true
    }
  }

  # User Data Script
  user_data = base64encode(templatefile("${path.module}/user_data.sh", {
    environment            = var.environment
    rds_secret_arn        = var.rds_secret_arn
    redis_secret_arn      = var.redis_secret_arn
    cloudwatch_log_group  = var.cloudwatch_log_group
    app_port              = var.app_port
  }))

  # Instance Metadata Service v2 (IMDSv2) - required for security
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  # Monitoring
  monitoring {
    enabled = var.enable_detailed_monitoring
  }

  tag_specifications {
    resource_type = "instance"
    tags = merge(
      {
        Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-app-instance"
        Environment = var.environment
        Purpose     = "Application server"
        ManagedBy   = "Auto Scaling Group"
      },
      var.tags
    )
  }

  tag_specifications {
    resource_type = "volume"
    tags = merge(
      {
        Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-app-volume"
        Environment = var.environment
      },
      var.tags
    )
  }

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-app-launch-template"
      Environment = var.environment
    },
    var.tags
  )

  lifecycle {
    create_before_destroy = true
  }
}

# Auto Scaling Group
resource "aws_autoscaling_group" "app" {
  name_prefix = "${var.resource_prefix}-${var.account_id}-${var.environment}-app-asg-"
  vpc_zone_identifier = var.subnet_ids
  target_group_arns   = [var.target_group_arn]
  
  min_size         = var.min_size
  max_size         = var.max_size
  desired_capacity = var.desired_capacity

  health_check_type         = "ELB"
  health_check_grace_period = 300
  default_cooldown          = 300

  enabled_metrics = [
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupMinSize",
    "GroupMaxSize",
    "GroupPendingInstances",
    "GroupStandbyInstances",
    "GroupTerminatingInstances",
    "GroupTotalInstances"
  ]

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
      instance_warmup        = 300
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.environment}-app-instance"
    propagate_at_launch = true
  }

  tag {
    key                 = "Environment"
    value               = var.environment
    propagate_at_launch = true
  }

  tag {
    key                 = "Purpose"
    value               = "Application server"
    propagate_at_launch = true
  }

  lifecycle {
    create_before_destroy = true
    ignore_changes = [
      desired_capacity  # Allow Auto Scaling to manage this
    ]
  }
}

# Auto Scaling Policy - Target Tracking (CPU)
resource "aws_autoscaling_policy" "cpu_target" {
  name                   = "${var.environment}-asg-cpu-target"
  autoscaling_group_name = aws_autoscaling_group.app.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ASGAverageCPUUtilization"
    }
    target_value = var.cpu_target_value
  }
}

# Auto Scaling Policy - Target Tracking (ALB Request Count)
resource "aws_autoscaling_policy" "alb_request_count" {
  name                   = "${var.environment}-asg-alb-request-target"
  autoscaling_group_name = aws_autoscaling_group.app.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = var.alb_target_group_label
    }
    target_value = var.request_count_target_value
  }
}

# CloudWatch Alarms for Auto Scaling
resource "aws_cloudwatch_metric_alarm" "asg_high_cpu" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-asg-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "This metric monitors EC2 CPU utilization"
  treat_missing_data  = "notBreaching"

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.app.name
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-asg-cpu-alarm"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "asg_low_healthy_hosts" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-asg-low-healthy-hosts"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "GroupInServiceInstances"
  namespace           = "AWS/AutoScaling"
  period              = 60
  statistic           = "Average"
  threshold           = var.min_size
  alarm_description   = "This metric monitors healthy instance count"
  treat_missing_data  = "breaching"

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.app.name
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-asg-healthy-hosts-alarm"
    Environment = var.environment
  }
}
