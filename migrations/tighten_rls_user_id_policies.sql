-- Follow-up migration: tighten RLS to require auth.uid() = user_id.
--
-- PREREQUISITES (do NOT apply until all are true):
--   1. migrations/add_user_id_ownership_columns.sql has been applied.
--   2. Existing rows on scan_runs / rollback_runs / environments / pending_applies
--      have been backfilled with the correct user_id (strategy is intentionally
--      NOT specified here — decide ownership explicitly before running).
--   3. Nullable user_id rows that should remain readable must either be
--      backfilled or accepted as invisible under these policies.
--
-- SPLIT: Column-add lives in add_user_id_ownership_columns.sql. This file
-- only replaces anon SELECT policies once ownership is known.
--
-- FLAG — tables listed in the task but WITHOUT a user_id column yet:
--   drift_events, drift_exception_registry, severity_routing_rules
--   cannot use auth.uid() = user_id until a separate column-add + backfill
--   migration exists for them. Their anon policies are LEFT UNCHANGED here
--   rather than guessing a user_id column or ownership model.
--
-- Intentionally NOT touched: environment_secrets, notification_secrets,
-- app_settings, scope_config (service-role-only; no anon policies).

-- ── scan_runs ──────────────────────────────────────────────────────────────
drop policy if exists "anon_select_only" on scan_runs;
create policy "authenticated_select_own"
on scan_runs
for select
to authenticated
using (auth.uid() = user_id);

-- ── rollback_runs ──────────────────────────────────────────────────────────
drop policy if exists "anon_select_only" on rollback_runs;
create policy "authenticated_select_own"
on rollback_runs
for select
to authenticated
using (auth.uid() = user_id);

-- ── pending_applies ────────────────────────────────────────────────────────
drop policy if exists "anon_select_only" on pending_applies;
create policy "authenticated_select_own"
on pending_applies
for select
to authenticated
using (auth.uid() = user_id);

-- ── environments ───────────────────────────────────────────────────────────
drop policy if exists "anon_active_only" on environments;
create policy "authenticated_select_own"
on environments
for select
to authenticated
using (auth.uid() = user_id and is_active = true);
