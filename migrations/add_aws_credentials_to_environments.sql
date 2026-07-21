-- Add AWS credential support to the environments table.
--
-- Non-secret fields (auth method, role ARN, external ID) go directly on
-- environments — these are safe for the anon-active RLS policy.  Actual
-- secrets (access key id, secret access key) live in a separate table
-- with zero anon access, exactly like notification_secrets.

-- 1. Non-secret auth columns on the existing environments table.
alter table environments
  add column if not exists auth_type         text check (auth_type in ('role', 'keys')),
  add column if not exists aws_role_arn      text,   -- OIDC role ARN for 'role' auth
  add column if not exists aws_external_id   text;   -- optional external ID for role assumption

-- 2. Secret key material — one row per environment, service-role only.
create table if not exists environment_secrets (
  environment_id        uuid not null unique references environments(id) on delete cascade,
  aws_access_key_id     text,
  aws_secret_access_key text,
  updated_at            timestamptz default now()
);

-- Enable RLS.
alter table environment_secrets enable row level security;

-- Intentionally create NO policies — this table has zero anon access.
-- Reads and writes MUST go through serve.py using the service-role key.
-- Verify with: select * from pg_policies where tablename = 'environment_secrets';
