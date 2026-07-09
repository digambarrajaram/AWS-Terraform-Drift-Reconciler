#!/bin/bash
# User Data Script for EC2 Instance Initialization
# This script runs on first boot to configure the application server

set -e  # Exit on any error

# Variables (templated by Terraform)
ENVIRONMENT="${environment}"
RDS_SECRET_ARN="${rds_secret_arn}"
REDIS_SECRET_ARN="${redis_secret_arn}"
CLOUDWATCH_LOG_GROUP="${cloudwatch_log_group}"
APP_PORT="${app_port}"
REGION="$(ec2-metadata --availability-zone | awk '{print $2}' | sed 's/[a-z]$//')"

# Log all output to user data log
exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "=== Starting EC2 instance initialization ==="
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo "Timestamp: $(date)"

# Update system packages
echo "=== Updating system packages ==="
yum update -y

# Install required packages
echo "=== Installing required packages ==="
yum install -y \
    aws-cli \
    amazon-cloudwatch-agent \
    jq \
    git \
    htop \
    curl \
    wget

# Install Docker (if using containerized application)
echo "=== Installing Docker ==="
yum install -y docker
systemctl enable docker
systemctl start docker
usermod -a -G docker ec2-user

# Install Node.js (example - adjust for your application)
echo "=== Installing Node.js ==="
curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
yum install -y nodejs

# Retrieve RDS credentials from Secrets Manager
echo "=== Retrieving RDS credentials ==="
RDS_SECRET=$(aws secretsmanager get-secret-value \
    --secret-id "$RDS_SECRET_ARN" \
    --region "$REGION" \
    --query 'SecretString' \
    --output text)

export DB_HOST=$(echo "$RDS_SECRET" | jq -r '.host')
export DB_PORT=$(echo "$RDS_SECRET" | jq -r '.port')
export DB_NAME=$(echo "$RDS_SECRET" | jq -r '.dbname')
export DB_USER=$(echo "$RDS_SECRET" | jq -r '.username')
export DB_PASSWORD=$(echo "$RDS_SECRET" | jq -r '.password')

# Retrieve Redis credentials from Secrets Manager (if AUTH enabled)
if [ -n "$REDIS_SECRET_ARN" ]; then
    echo "=== Retrieving Redis credentials ==="
    REDIS_SECRET=$(aws secretsmanager get-secret-value \
        --secret-id "$REDIS_SECRET_ARN" \
        --region "$REGION" \
        --query 'SecretString' \
        --output text)

    export REDIS_ENDPOINT=$(echo "$REDIS_SECRET" | jq -r '.endpoint')
    export REDIS_PORT=$(echo "$REDIS_SECRET" | jq -r '.port')
    export REDIS_AUTH_TOKEN=$(echo "$REDIS_SECRET" | jq -r '.auth_token // empty')
fi

# Create application configuration file
echo "=== Creating application configuration ==="
mkdir -p /opt/app
cat > /opt/app/.env <<EOF
NODE_ENV=$ENVIRONMENT
APP_PORT=$APP_PORT

# Database Configuration
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD

# Redis Configuration
REDIS_ENDPOINT=$REDIS_ENDPOINT
REDIS_PORT=$REDIS_PORT
REDIS_AUTH_TOKEN=$REDIS_AUTH_TOKEN

# AWS Configuration
AWS_REGION=$REGION
CLOUDWATCH_LOG_GROUP=$CLOUDWATCH_LOG_GROUP
EOF

chmod 600 /opt/app/.env

# Configure CloudWatch Agent
echo "=== Configuring CloudWatch Agent ==="
cat > /opt/aws/amazon-cloudwatch-agent/etc/cloudwatch-config.json <<EOF
{
  "agent": {
    "metrics_collection_interval": 60,
    "run_as_user": "cwagent"
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/user-data.log",
            "log_group_name": "$CLOUDWATCH_LOG_GROUP",
            "log_stream_name": "{instance_id}/user-data.log"
          },
          {
            "file_path": "/opt/app/logs/application.log",
            "log_group_name": "$CLOUDWATCH_LOG_GROUP",
            "log_stream_name": "{instance_id}/application.log"
          }
        ]
      }
    }
  },
  "metrics": {
    "namespace": "CustomApp/$ENVIRONMENT",
    "metrics_collected": {
      "cpu": {
        "measurement": [
          {"name": "cpu_usage_idle", "rename": "CPU_IDLE", "unit": "Percent"},
          {"name": "cpu_usage_iowait", "rename": "CPU_IOWAIT", "unit": "Percent"}
        ],
        "metrics_collection_interval": 60,
        "totalcpu": false
      },
      "disk": {
        "measurement": [
          {"name": "used_percent", "rename": "DISK_USED", "unit": "Percent"}
        ],
        "metrics_collection_interval": 60,
        "resources": ["/"]
      },
      "mem": {
        "measurement": [
          {"name": "mem_used_percent", "rename": "MEM_USED", "unit": "Percent"}
        ],
        "metrics_collection_interval": 60
      }
    }
  }
}
EOF

# Start CloudWatch Agent
systemctl enable amazon-cloudwatch-agent
systemctl start amazon-cloudwatch-agent

# Create systemd service for application (example)
echo "=== Creating application systemd service ==="
cat > /etc/systemd/system/app.service <<EOF
[Unit]
Description=Application Server
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/app
EnvironmentFile=/opt/app/.env
ExecStart=/usr/bin/node /opt/app/server.js
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# NOTE: Application deployment should be handled by CI/CD pipeline
# This is just a placeholder - actual application code should be deployed separately

# Enable application service (will start when application is deployed)
systemctl daemon-reload
# systemctl enable app.service

echo "=== EC2 instance initialization complete ==="
echo "Instance is ready for application deployment"
