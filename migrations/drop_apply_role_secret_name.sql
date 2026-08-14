-- Drop apply_role_secret_name from environments.
--
-- The CI workflow no longer reads the role ARN from a per-scope GitHub
-- Secret name — resolve-scope fetches aws_role_arn directly from
-- Supabase now.  The column is dead weight.

alter table environments drop column if exists apply_role_secret_name;
