-- Drift Exception Registry — replaces file-based drift-exceptions.json and
-- unmanaged-exceptions.json with a single Supabase table.  The pipeline reads
-- directly from this table; the dashboard adds/expires/deletes rows directly
-- — no GitHub PR round-trip.

create table if not exists drift_exception_registry (
  id                    uuid primary key default gen_random_uuid(),
  scope                 text not null,
  exception_type        text not null,           -- 'drift' or 'unmanaged'
  resource_address      text,                    -- drift: aws_instance.example
  drift_type            text default '*',        -- drift: field name, 'ingress', 'egress', '*', etc.
  resource_type         text,                    -- unmanaged: aws_security_group
  resource_id_pattern   text,                    -- unmanaged: launch-wizard
  reason                text not null,           -- mandatory human-readable justification
  approved_by           text,
  expires               date,                    -- optional ISO date; null = permanent
  auto                  boolean default false,   -- drift: skip human review
  max_monthly_cost_usd  numeric(10,2),           -- unmanaged: cost cap (suppress only if below)
  active                boolean default true,    -- soft-delete
  created_at            timestamptz default now()
);

-- RLS — same pattern as drift_events: anon can read, service_role bypasses.
alter table drift_exception_registry enable row level security;

drop policy if exists "anon_select_only" on drift_exception_registry;
create policy "anon_select_only" on drift_exception_registry
for select
to anon
using (true);
