-- Enable Row Level Security on drift_events so the anon role
-- (used by the live dashboard) can only SELECT — no inserts,
-- updates, deletes, or schema changes.
--
-- Run this in the Supabase SQL Editor (https://app.supabase.com).

-- 1. Enable RLS on the table.
alter table drift_events enable row level security;

-- 2. Drop any existing policy on the table (idempotent re-run).
drop policy if exists "anon_select_only" on drift_events;

-- 3. Allow the anon role to read all rows, nothing else.
create policy "anon_select_only"
on drift_events
for select
to anon
using (true);

-- The service_role (used by the Python pipeline) bypasses RLS
-- automatically — no policy needed for it.
