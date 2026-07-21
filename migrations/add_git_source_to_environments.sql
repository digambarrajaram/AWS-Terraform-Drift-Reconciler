-- Add git-clone source metadata to environments, so the server can
-- clone a fresh copy of the Terraform code for each scan instead of
-- relying on a pre-existing local directory.
--
-- tf_directory_path stays as-is — it becomes the SUBPATH within the
-- clone where terraform commands run (e.g. "terraform_code/ec2_...").
--
-- The actual GitHub token (if needed for private repos) goes on
-- environment_secrets, same zero-RLS pattern as AWS keys.

-- 1. Git source metadata on environments (safe for anon read).
alter table environments
  add column if not exists repo_url       text,   -- https://github.com/org/repo.git
  add column if not exists repo_branch    text default 'main',
  add column if not exists git_auth_type  text check (git_auth_type in ('none', 'token')),
  add column if not exists clone_path     text;   -- populated on first clone, e.g. /var/drift-clones/<slug>

-- 2. GitHub token goes on the secrets table (zero anon access).
--    Create the table if it doesn't already exist (from the earlier
--    AWS credentials migration — this migration is safe to run first).
create table if not exists environment_secrets (
  environment_id        uuid not null unique references environments(id) on delete cascade,
  aws_access_key_id     text,
  aws_secret_access_key text,
  github_token          text,
  updated_at            timestamptz default now()
);

alter table environment_secrets enable row level security;
-- Intentionally create NO policies — zero anon access.

-- Add the column if the table already existed without it.
alter table environment_secrets
  add column if not exists github_token text;
