-- App-wide secrets singleton.  Service-role only — no anon access via RLS.
-- The GitHub token lives here so the dashboard can update it without
-- touching .env or restarting serve.py.  (The per-environment
-- environment_secrets.github_token is for git-clone auth on individual
-- environments — this is the global GITHUB_TOKEN used for PR creation,
-- drift history, and GitHub API calls.)

create table if not exists app_settings (
  id              integer primary key default 1
                    check (id = 1),
  github_token    text,
  updated_at      timestamptz default now()
);

-- Seed the singleton row so an upsert-style update always works without
-- the caller needing to check whether a row already exists.
insert into app_settings (id) values (1)
on conflict (id) do nothing;

-- Enable RLS.
alter table app_settings enable row level security;

-- Intentionally create NO policies — this table has zero anon access.
-- The dashboard reads/writes through serve.py's service-role key only.
