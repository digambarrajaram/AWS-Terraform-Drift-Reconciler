-- MANUAL BACKFILL TEMPLATE — do NOT auto-run.
--
-- FLAG: Existing environments / scan_runs / rollback_runs / pending_applies
-- rows have NULL user_id. Ownership must be decided explicitly per row (or
-- per environment slug). Do NOT assign to "whoever logs in first".
--
-- Prerequisites:
--   1. migrations/add_user_id_ownership_columns.sql applied
--   2. You know which auth.users.id owns which environment slug
--
-- After this backfill succeeds, apply:
--   migrations/tighten_rls_user_id_policies.sql
--
-- ---------------------------------------------------------------------------
-- Step 0 — discover candidates (read-only)
-- ---------------------------------------------------------------------------
-- select id, email from auth.users order by created_at;
-- select id, slug, name, user_id from environments order by created_at;
-- select count(*) as null_owners from environments where user_id is null;

-- ---------------------------------------------------------------------------
-- Step 1 — assign each environment to an owner (EDIT PLACEHOLDERS)
-- ---------------------------------------------------------------------------
-- Replace <OWNER_USER_UUID> and <ENV_SLUG> for each environment you keep.
-- Repeat the UPDATE once per environment.

-- update environments
-- set user_id = '<OWNER_USER_UUID>'::uuid
-- where slug = '<ENV_SLUG>'
--   and user_id is null;

-- Example (delete before use):
-- update environments set user_id = '00000000-0000-0000-0000-000000000000'::uuid
-- where slug = 'scope-a' and user_id is null;

-- ---------------------------------------------------------------------------
-- Step 2 — propagate environment owner onto downstream rows by scope
-- ---------------------------------------------------------------------------
-- scan_runs.scope / rollback_runs.scope / pending_applies.scope match
-- environments.slug.

-- update scan_runs sr
-- set user_id = e.user_id
-- from environments e
-- where sr.scope = e.slug
--   and sr.user_id is null
--   and e.user_id is not null;

-- update rollback_runs rr
-- set user_id = e.user_id
-- from environments e
-- where rr.scope = e.slug
--   and rr.user_id is null
--   and e.user_id is not null;

-- update pending_applies pa
-- set user_id = e.user_id
-- from environments e
-- where pa.scope = e.slug
--   and pa.user_id is null
--   and e.user_id is not null;

-- ---------------------------------------------------------------------------
-- Step 3 — verify before enabling RLS tighten migration
-- ---------------------------------------------------------------------------
-- select 'environments' as tbl, count(*) from environments where user_id is null
-- union all select 'scan_runs', count(*) from scan_runs where user_id is null
-- union all select 'rollback_runs', count(*) from rollback_runs where user_id is null
-- union all select 'pending_applies', count(*) from pending_applies where user_id is null;
--
-- Expect 0 nulls for rows you still care about. Orphan rows (scope with no
-- matching environment) need a manual decision — do not auto-delete here.
