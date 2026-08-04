alter table environment_secrets
  add column if not exists apply_role_arn text;

-- No RLS policy changes needed — environment_secrets already has zero
-- anon access (see add_aws_credentials_to_environments.sql). This column
-- follows the same service-role-only pattern as aws_secret_access_key
-- and github_token.
