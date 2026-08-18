-- Table: pending_applies — one row per merged drift-fix PR awaiting a
-- dashboard-side apply decision.  Replaces the GitHub Environment
-- approval gate once apply moves out of GitHub Actions.

create table if not exists pending_applies (
  id              uuid primary key default gen_random_uuid(),
  pr_number       integer not null,
  scope           text not null,
  status          text default 'awaiting_approval',
  merged_at       timestamptz,
  approved_by     text,
  approved_at     timestamptz,
  applied_at      timestamptz,
  result          jsonb,
  created_at      timestamptz default now()
);

-- Same RLS convention as rollback_runs: anon can SELECT, service_role
-- bypasses RLS for writes.
alter table pending_applies enable row level security;

drop policy if exists "anon_select_only" on pending_applies;

create policy "anon_select_only"
on pending_applies
for select
to anon
using (true);
