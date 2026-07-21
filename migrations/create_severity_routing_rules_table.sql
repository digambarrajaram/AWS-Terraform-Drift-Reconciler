-- Severity → notification channel routing rules.
-- scope=NULL means "default for all scopes."  A scope-specific row
-- overrides the default for that scope.

create table if not exists severity_routing_rules (
  id          integer primary key generated always as identity,
  severity    text not null,
  channel     text not null,        -- 'pagerduty', 'slack', 'none'
  scope       text,                 -- null = default for all scopes
  updated_at  timestamptz default now()
);

-- Seed defaults matching the current hardcoded logic in agent.py.
insert into severity_routing_rules (severity, channel) values
  ('HIGH',   'pagerduty'),
  ('MEDIUM', 'slack'),
  ('LOW',    'slack')
on conflict do nothing;

-- RLS — anon can read, service_role bypasses.
alter table severity_routing_rules enable row level security;

drop policy if exists "anon_select_only" on severity_routing_rules;
create policy "anon_select_only" on severity_routing_rules
for select
to anon
using (true);
