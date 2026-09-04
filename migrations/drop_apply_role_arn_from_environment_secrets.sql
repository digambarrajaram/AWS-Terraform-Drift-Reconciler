-- Drop unused apply_role_arn from environment_secrets.
-- Confirmed zero consumers in application code (role ARN lives on
-- environments.aws_role_arn). Safe to drop.

alter table environment_secrets
  drop column if exists apply_role_arn;
