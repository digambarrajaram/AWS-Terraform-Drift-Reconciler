-- Scope wiring metadata — non-secret variable names and identifiers
-- for each scope.  Readable by anon (no secrets), writes are service-role only.

create table if not exists scope_config (
  scope                     text primary key,
  region_variable           text not null,        -- GitHub Variable name for region
  scan_role_variable        text not null,        -- GitHub Variable name for scan role ARN
  apply_role_secret_name    text not null,        -- GitHub Secret name for apply role ARN
  apply_environment_name    text not null,        -- GitHub Environment name for apply gate
  tf_state_bucket           text not null,        -- S3 bucket for terraform state
  aws_account_id            text,                 -- AWS account ID (same for both scopes currently)
  updated_at                timestamptz default now()
);

-- Seed confirmed values from drift-reconciler.yml and backend.tf.
insert into scope_config (scope, region_variable, scan_role_variable, apply_role_secret_name, apply_environment_name, tf_state_bucket, aws_account_id)
values
  ('scope-a', 'PROD_A_REGION', 'SCOPE_A_SCAN_ROLE_ARN', 'SCOPE_A_APPLY_ROLE_ARN', 'scope-a-apply', 'scope-a-tf-state-605134452604', '605134452604'),
  ('scope-b', 'PROD_B_REGION', 'SCOPE_B_SCAN_ROLE_ARN', 'SCOPE_B_APPLY_ROLE_ARN', 'scope-b-apply', 'scope-b-tf-state-605134452604', '605134452604')
on conflict (scope) do nothing;

-- RLS — anon can read, service_role bypasses for writes.
alter table scope_config enable row level security;

drop policy if exists "anon_select_only" on scope_config;
create policy "anon_select_only" on scope_config
for select
to anon
using (true);
