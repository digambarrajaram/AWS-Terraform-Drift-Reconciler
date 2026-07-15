# bootstrap/scan-role.tf
#
# Read-only role for drift DETECTION (terraform plan). Runs unattended on
# every workflow trigger -- no approval gate needed, because it can't
# change anything. Trust is scoped to a branch (not an environment), since
# this job isn't meant to be manually gated.
#
# GitHub variable name: PROD_A_SCAN_ROLE_ARN / PROD_B_SCAN_ROLE_ARN
# Store in: repo-level Variables (not an Environment)

data "aws_iam_policy_document" "scan_trust" {
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
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:ref:refs/heads/${var.scan_allowed_branch}"]
    }
  }
}

resource "aws_iam_role" "scan" {
  name               = "drift-reconciler-scan-${var.account_label}"
  assume_role_policy = data.aws_iam_policy_document.scan_trust.json

  tags = {
    Purpose = "drift-reconciler-scan"
    Account = var.account_label
  }
}

# Broad read access -- covers EC2/S3/DynamoDB/VPC/everything else `terraform
# plan` needs to evaluate drift. Read-only, so breadth here is low-risk
# (unlike the apply role, where breadth would be a real problem).
resource "aws_iam_role_policy_attachment" "scan_readonly" {
  role       = aws_iam_role.scan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# terraform plan takes a state lock even though it doesn't write resources,
# so this role needs read+lock access to state, not just read.
data "aws_iam_policy_document" "scan_state_access" {
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
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

resource "aws_iam_role_policy" "scan_state_access" {
  name   = "tf-state-access"
  role   = aws_iam_role.scan.id
  policy = data.aws_iam_policy_document.scan_state_access.json
}

output "scan_role_arn" {
  value = aws_iam_role.scan.arn
}
