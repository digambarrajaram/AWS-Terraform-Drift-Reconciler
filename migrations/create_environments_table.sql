-- Environments table — replaces scope_config with a richer schema that
-- holds all wiring metadata for each drift-reconciler scope/environment.
-- Anon can read active environments; writes are service-role only.

create table if not exists environments (
  id                       uuid primary key default gen_random_uuid(),
  name                     text not null,          -- human label: "Production A"
  slug                     text not null unique,   -- machine key: "scope-a"
  aws_account_id           text,
  aws_profile              text,                   -- AWS named profile: "account-a"
  region                   text,                   -- AWS region: "us-east-1"
  tf_state_bucket          text,                   -- S3 backend bucket
  tf_lock_table            text default 'terraform-locks',
  tf_directory_path        text,                   -- repo-relative terraform dir
  scan_role_variable       text,                   -- GitHub Variable name for scan role ARN
  apply_role_secret_name   text,                   -- GitHub Secret name for apply role ARN
  apply_environment_name   text,                   -- GitHub Environment for approval gate
  is_active                boolean default true,
  created_at               timestamptz default now(),
  updated_at               timestamptz default now()
);

-- Seed confirmed values from drift-reconciler.yml, backend.tf, and GitHub.
insert into environments (
  slug, name, aws_account_id, aws_profile, region,
  tf_state_bucket, tf_directory_path,
  scan_role_variable, apply_role_secret_name, apply_environment_name
) values
  (
    'scope-a', 'Production A', '605134452604', 'account-a', 'us-east-1',
    'scope-a-tf-state-605134452604', 'terraform_code/ec2_terraform_account_a',
    'SCOPE_A_SCAN_ROLE_ARN', 'SCOPE_A_APPLY_ROLE_ARN', 'scope-a-apply'
  ),
  (
    'scope-b', 'Production B', '605134452604', 'account-b', 'us-west-2',
    'scope-b-tf-state-605134452604', 'terraform_code/ec2_terraform_account_b',
    'SCOPE_B_SCAN_ROLE_ARN', 'SCOPE_B_APPLY_ROLE_ARN', 'scope-b-apply'
  )
on conflict (slug) do nothing;

-- RLS — anon can read active environments only.
alter table environments enable row level security;

drop policy if exists "anon_active_only" on environments;
create policy "anon_active_only" on environments
for select
to anon
using (is_active = true);
