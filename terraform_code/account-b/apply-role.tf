# bootstrap/apply-role.tf
#
# Write-scoped role for RESOLVING drift (terraform apply on an approved fix).
# Trust is scoped to a GitHub ENVIRONMENT, not just a branch -- when a job
# targets a protected environment, its OIDC token's `sub` claim becomes
# repo:org/repo:environment:<name> instead of the branch-based one. That
# means this role is unassumable unless the job actually went through the
# environment's required-reviewer approval gate. Configure that gate in
# GitHub: Settings -> Environments -> <apply_environment_name> ->
# required reviewers.
#
# GitHub variable name: PROD_A_APPLY_ROLE_ARN / PROD_B_APPLY_ROLE_ARN
# Store in: the matching protected Environment's Variables (NOT repo-level)

data "aws_iam_policy_document" "apply_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:environment:${var.apply_environment_name}"]
    }
  }
}

resource "aws_iam_role" "apply" {
  name               = "drift-reconciler-apply-${var.account_label}"
  assume_role_policy = data.aws_iam_policy_document.apply_trust.json

  tags = {
    Purpose = "drift-reconciler-apply"
    Account = var.account_label
  }
}

# ---- EC2 + VPC write permissions ----
# VPC resources (subnets, route tables, IGW, NAT, the VPC itself) are all
# under the ec2: action namespace in AWS IAM -- there's no separate "vpc:"
# prefix, so these live in one statement.
#
# NOTE: most EC2/VPC actions do NOT support resource-level ARN restriction
# (a real AWS IAM limitation, not a choice made here) -- they require
# Resource = "*". This is narrower than ec2:* (only the specific actions
# your .tf actually performs), but can't be scoped by resource the way
# S3/DynamoDB below can. If you add resource types beyond what's listed
# here, this list needs updating to match.
data "aws_iam_policy_document" "apply_ec2_vpc" {
  statement {
    sid    = "EC2InstanceWrite"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:TerminateInstances",
      "ec2:StartInstances",
      "ec2:StopInstances",
      "ec2:ModifyInstanceAttribute",
      "ec2:CreateTags",
      "ec2:DeleteTags",
      "ec2:CreateVolume",
      "ec2:DeleteVolume",
      "ec2:AttachVolume",
      "ec2:DetachVolume",
      "ec2:ModifyVolume",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "EC2SecurityGroupWrite"
    effect = "Allow"
    actions = [
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
      "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
      "ec2:ModifySecurityGroupRules",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "VPCNetworkingWrite"
    effect = "Allow"
    actions = [
      "ec2:CreateVpc",
      "ec2:DeleteVpc",
      "ec2:ModifyVpcAttribute",
      "ec2:CreateSubnet",
      "ec2:DeleteSubnet",
      "ec2:ModifySubnetAttribute",
      "ec2:CreateRouteTable",
      "ec2:DeleteRouteTable",
      "ec2:CreateRoute",
      "ec2:DeleteRoute",
      "ec2:ReplaceRoute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:CreateInternetGateway",
      "ec2:DeleteInternetGateway",
      "ec2:AttachInternetGateway",
      "ec2:DetachInternetGateway",
      "ec2:CreateNatGateway",
      "ec2:DeleteNatGateway",
      "ec2:AllocateAddress",
      "ec2:ReleaseAddress",
      "ec2:AssociateAddress",
      "ec2:DisassociateAddress",
    ]
    resources = ["*"]
  }

  # Read permissions for terraform refresh — terraform plan/apply refreshes
  # every resource before acting, so the apply role needs Describe*/Get*
  # alongside its write actions or the implicit refresh fails with AccessDenied.
  statement {
    sid    = "EC2VPCRead"
    effect = "Allow"
    actions = [
      "ec2:Describe*",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "apply_ec2_vpc" {
  name   = "ec2-vpc-write"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply_ec2_vpc.json
}

# ---- S3 write permissions (managed infra buckets, NOT the state bucket) ----
# Scoped by naming prefix -- only buckets this project creates, not every
# bucket in the account. Adjust managed_resource_prefix to match your
# actual bucket naming convention.
data "aws_iam_policy_document" "apply_s3" {
  statement {
    sid    = "S3ManagedBucketWrite"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:PutBucketPolicy",
      "s3:PutBucketVersioning",
      "s3:PutEncryptionConfiguration",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketTagging",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:GetBucketLocation",
      "s3:ListBucket",
    ]
    resources = [
      "arn:aws:s3:::${var.managed_resource_prefix}*",
      "arn:aws:s3:::${var.managed_resource_prefix}*/*",
    ]
  }

  # Read permissions for terraform refresh.
  statement {
    sid    = "S3ManagedBucketRead"
    effect = "Allow"
    actions = [
      "s3:Get*",
      "s3:List*",
    ]
    resources = [
      "arn:aws:s3:::${var.managed_resource_prefix}*",
      "arn:aws:s3:::${var.managed_resource_prefix}*/*",
    ]
  }
}

resource "aws_iam_role_policy" "apply_s3" {
  name   = "s3-write"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply_s3.json
}

# ---- DynamoDB write permissions (managed app tables, NOT the lock table) ----
data "aws_iam_policy_document" "apply_dynamodb" {
  statement {
    sid    = "DynamoDBManagedTableWrite"
    effect = "Allow"
    actions = [
      "dynamodb:CreateTable",
      "dynamodb:DeleteTable",
      "dynamodb:UpdateTable",
      "dynamodb:DescribeTable",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:UpdateTimeToLive",
      "dynamodb:UpdateContinuousBackups",
    ]
    resources = [
      "arn:aws:dynamodb:${var.aws_region}:*:table/${var.managed_resource_prefix}*",
    ]
  }

  # Read permissions for terraform refresh.
  statement {
    sid    = "DynamoDBManagedTableRead"
    effect = "Allow"
    actions = [
      "dynamodb:Describe*",
    ]
    resources = [
      "arn:aws:dynamodb:${var.aws_region}:*:table/${var.managed_resource_prefix}*",
    ]
  }
}

resource "aws_iam_role_policy" "apply_dynamodb" {
  name   = "dynamodb-write"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply_dynamodb.json
}

# ---- Lambda + CloudWatch Logs write permissions ----
# Log groups for Lambda live under /aws/lambda/<function-name>. IAM read
# actions (DescribeLogGroups, etc.) require Resource = "*" — same pattern as
# ec2:Describe* above.
data "aws_iam_policy_document" "apply_lambda" {
  statement {
    sid    = "LambdaFunctionWrite"
    effect = "Allow"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PublishVersion",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:TagResource",
      "lambda:UntagResource",
    ]
    resources = [
      "arn:aws:lambda:${var.aws_region}:*:function:${var.managed_resource_prefix}*",
    ]
  }

  statement {
    sid    = "LambdaLogGroupWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.managed_resource_prefix}*",
      "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.managed_resource_prefix}*:*",
    ]
  }

  statement {
    sid    = "LambdaExecutionRoleWrite"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
    ]
    resources = [
      "arn:aws:iam::*:role/${var.managed_resource_prefix}*",
    ]
  }

  statement {
    sid    = "LambdaExecutionRolePass"
    effect = "Allow"
    actions = [
      "iam:PassRole",
    ]
    resources = [
      "arn:aws:iam::*:role/${var.managed_resource_prefix}*",
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  statement {
    sid    = "LambdaRead"
    effect = "Allow"
    actions = [
      "lambda:Get*",
      "lambda:List*",
    ]
    resources = [
      "arn:aws:lambda:${var.aws_region}:*:function:${var.managed_resource_prefix}*",
    ]
  }

  statement {
    sid    = "LambdaLogsRead"
    effect = "Allow"
    actions = [
      "logs:Describe*",
      "logs:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "LambdaExecutionRoleRead"
    effect = "Allow"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = [
      "arn:aws:iam::*:role/${var.managed_resource_prefix}*",
    ]
  }
}

resource "aws_iam_role_policy" "apply_lambda" {
  name   = "lambda-write"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply_lambda.json
}

# ---- Terraform state access (write -- apply modifies remote state) ----
data "aws_iam_policy_document" "apply_state_access" {
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      "arn:aws:s3:::${var.state_bucket_name}",
      "arn:aws:s3:::${var.state_bucket_name}/*",
    ]
  }
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = ["arn:aws:dynamodb:${var.aws_region}:*:table/${var.lock_table_name}"]
  }
}

resource "aws_iam_role_policy" "apply_state_access" {
  name   = "tf-state-access"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply_state_access.json
}

output "apply_role_arn" {
  value = aws_iam_role.apply.arn
}
