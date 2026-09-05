-- Add user_id ownership to drift_exception_registry.
--
-- Join path: drift_exception_registry.scope = environments.slug
-- (scope is the environment machine key; see backfill_user_id_ownership.sql).
--
-- PREREQUISITE: environments.user_id backfilled for rows whose scope matches
-- an exception's scope value.
--
-- ---------------------------------------------------------------------------
-- Step 0 — pre-check (read-only)
-- ---------------------------------------------------------------------------
-- Orphan exceptions (no matching environment slug):
-- select r.id, r.scope
-- from drift_exception_registry r
-- left join environments e on e.slug = r.scope
-- where e.id is null;
--
-- Duplicate active natural keys per owner after backfill (expect zero):
-- select r.user_id, r.scope, r.exception_type, r.resource_address, r.drift_type, count(*)
-- from drift_exception_registry r
-- where r.active = true and r.exception_type = 'drift'
-- group by 1,2,3,4,5 having count(*) > 1;
--
-- ---------------------------------------------------------------------------
-- Step 1 — add nullable column
-- ---------------------------------------------------------------------------
alter table drift_exception_registry
  add column if not exists user_id uuid references auth.users (id);

create index if not exists drift_exception_registry_user_id_idx
  on drift_exception_registry (user_id);

-- ---------------------------------------------------------------------------
-- Step 2 — backfill from owning environment
-- ---------------------------------------------------------------------------
update drift_exception_registry r
set user_id = e.user_id
from environments e
where r.scope = e.slug
  and r.user_id is null
  and e.user_id is not null;

-- ---------------------------------------------------------------------------
-- Step 3 — enforce NOT NULL (fails if orphans remain — resolve manually)
-- ---------------------------------------------------------------------------
alter table drift_exception_registry
  alter column user_id set not null;
