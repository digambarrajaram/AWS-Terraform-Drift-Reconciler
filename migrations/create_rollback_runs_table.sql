-- Table: rollback_runs — tracks each rollback invocation so the frontend
-- can show live progress and historical rollback results.

create table if not exists rollback_runs (
  id              uuid primary key default gen_random_uuid(),
  pr_number       integer not null,
  scope           text not null,
  mode            text default 'preview',
  status          text default 'running',
  current_stage   text,
  started_at      timestamptz default now(),
  completed_at    timestamptz,
  result          jsonb,
  rollback_pr_url text
);

alter table rollback_runs enable row level security;

drop policy if exists "anon_select_only" on rollback_runs;

create policy "anon_select_only"
on rollback_runs
for select
to anon
using (true);
