# Application Load Balancer Module - Main Configuration

terraform {
  required_version = ">= 1.6.0"
}

# Application Load Balancer
resource "aws_lb" "main" {
  name               = "${var.resource_prefix}-${var.environment}-app-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.security_group_id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection = var.enable_deletion_protection
  enable_http2               = true
  enable_cross_zone_load_balancing = true

  access_logs {
    bucket  = var.access_logs_bucket
    prefix  = var.access_logs_prefix
    enabled = var.access_logs_bucket != ""
  }

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-app-alb"
      Environment = var.environment
      Purpose     = "Application load balancing across AZs"
    },
    var.tags
  )
}

# Target Group for EC2 Instances
resource "aws_lb_target_group" "app" {
  name     = "${var.resource_prefix}-${var.environment}-app-tg"
  port     = var.app_port
  protocol = "HTTP"
  vpc_id   = var.vpc_id

  health_check {
    enabled             = true
    healthy_threshold   = 3
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    path                = var.health_check_path
    matcher             = "200"
    protocol            = "HTTP"
  }

  deregistration_delay = 30

  stickiness {
    type            = "lb_cookie"
    enabled         = var.enable_sticky_sessions
    cookie_duration = 86400  # 24 hours
  }

  tags = merge(
    {
      Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-app-tg"
      Environment = var.environment
      Purpose     = "ALB target group for application servers"
    },
    var.tags
  )
}

# HTTPS Listener (primary)
resource "aws_lb_listener" "https" {
  count             = var.certificate_arn != "" ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-https-listener"
  }
}

# HTTP Listener (redirect to HTTPS when certificate available)
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = var.certificate_arn != "" ? "redirect" : "forward"

    # Redirect to HTTPS if certificate available
    dynamic "redirect" {
      for_each = var.certificate_arn != "" ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }

    # Forward to target group if no certificate
    target_group_arn = var.certificate_arn != "" ? null : aws_lb_target_group.app.arn
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-http-listener"
  }
}

# Additional HTTPS Listener Rule (optional custom routing)
resource "aws_lb_listener_rule" "host_based_routing" {
  count        = length(var.additional_hostnames) > 0 && var.certificate_arn != "" ? 1 : 0
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    host_header {
      values = var.additional_hostnames
    }
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-host-based-rule"
  }
}

# CloudWatch Alarms for ALB
resource "aws_cloudwatch_metric_alarm" "alb_target_response_time" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-high-response-time"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 1.0  # 1 second
  alarm_description   = "This metric monitors ALB target response time"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-response-time-alarm"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_hosts" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-unhealthy-hosts"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 0
  alarm_description   = "This metric monitors unhealthy target count"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TargetGroup  = aws_lb_target_group.app.arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }

  tags = {
    Name        = "${var.resource_prefix}-${var.account_id}-${var.environment}-alb-unhealthy-hosts-alarm"
    Environment = var.environment
  }
}
