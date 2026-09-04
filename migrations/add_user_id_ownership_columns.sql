-- Migration: add nullable user_id ownership columns for per-user Auth.
--
-- SPLIT: This file ONLY adds columns. Do NOT apply RLS policy changes here.
-- Follow-up: migrations/tighten_rls_user_id_policies.sql (apply AFTER backfill).
--
-- FLAG — backfill required, no default guessed:
--   user_id is nullable intentionally. Existing rows have no owner.
--   Do NOT invent a default user or backfill strategy here — decide ownership
--   explicitly, then backfill before enabling the follow-up RLS migration.
--
-- Tables receiving user_id: scan_runs, rollback_runs, environments, pending_applies.
-- Intentionally NOT touched: environment_secrets, notification_secrets,
-- app_settings, scope_config (remain service-role-only).

alter table scan_runs
  add column if not exists user_id uuid references auth.users (id);

alter table rollback_runs
  add column if not exists user_id uuid references auth.users (id);

alter table environments
  add column if not exists user_id uuid references auth.users (id);

alter table pending_applies
  add column if not exists user_id uuid references auth.users (id);

-- Indexes to support future owner-scoped queries / RLS.
create index if not exists scan_runs_user_id_idx on scan_runs (user_id);
create index if not exists rollback_runs_user_id_idx on rollback_runs (user_id);
create index if not exists environments_user_id_idx on environments (user_id);
create index if not exists pending_applies_user_id_idx on pending_applies (user_id);
