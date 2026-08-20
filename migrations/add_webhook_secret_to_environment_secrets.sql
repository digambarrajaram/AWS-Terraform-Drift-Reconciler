-- Add per-environment webhook secret to environment_secrets table.
-- Allows each environment to have its own GitHub webhook secret for
-- signature verification, falling back to the global GITHUB_TOKEN if not set.

alter table environment_secrets
  add column if not exists webhook_secret text;

-- No migration of existing data — webhook_secret starts NULL for all rows.
-- Fallback behavior: if webhook_secret is NULL, use global GITHUB_TOKEN.
