-- Singleton secrets row.  Service-role only — no anon access via RLS.
-- PagerDuty and Slack credentials live here so the dashboard can
-- update them without touching .env or restarting serve.py.

create table if not exists notification_secrets (
  id                      integer primary key default 1
                            check (id = 1),
  pagerduty_routing_key   text,
  slack_webhook_url       text,
  updated_at              timestamptz default now()
);

-- Seed the singleton row so an upsert-style update always works without
-- the caller needing to check whether a row already exists.
insert into notification_secrets (id) values (1)
on conflict (id) do nothing;

-- Enable RLS.
alter table notification_secrets enable row level security;

-- Intentionally create NO policies — this table has zero anon access.
-- Verify with: select * from pg_policies where tablename = 'notification_secrets';
-- (After running the migration, the anon key should see zero rows.)
