-- Table: scan_runs — tracks each pipeline invocation so the frontend
-- can show live progress and historical run results.

create table if not exists scan_runs (
  id              uuid primary key default gen_random_uuid(),
  scope           text not null,
  unmanaged_flag  boolean default false,
  status          text default 'running',
  current_stage   text,
  started_at      timestamptz default now(),
  completed_at    timestamptz,
  result_summary  jsonb,
  pr_links        jsonb default '[]'
);

-- Enable RLS — same pattern as drift_events.
alter table scan_runs enable row level security;

drop policy if exists "anon_select_only" on scan_runs;

create policy "anon_select_only"
on scan_runs
for select
to anon
using (true);
