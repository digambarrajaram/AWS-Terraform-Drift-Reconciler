# bootstrap/oidc-provider.tf
#
# An AWS account can only have ONE GitHub OIDC provider registered total.
# If this account already has one (from this or another project), set
# create_oidc_provider = false and pass its ARN via existing_oidc_provider_arn
# instead of creating a second one (which will fail with "already exists").
#
# Check first with:
#   aws iam list-open-id-connect-providers --profile <account-profile>

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = {
    Purpose = "github-actions-oidc"
  }
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_oidc_provider_arn
}
