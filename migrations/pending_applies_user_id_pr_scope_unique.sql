-- Owner-scoped pending_applies identity: (user_id, pr_number, scope).
--
-- PREREQUISITE: migrations/add_user_id_ownership_columns.sql applied and
-- pending_applies.user_id backfilled from environments (see
-- migrations/backfill_user_id_ownership.sql).
--
-- ---------------------------------------------------------------------------
-- Step 0 — pre-check (read-only; expect zero rows before applying)
-- ---------------------------------------------------------------------------
-- select user_id, pr_number, scope, count(*) as n
-- from pending_applies
-- group by user_id, pr_number, scope
-- having count(*) > 1;
--
-- ---------------------------------------------------------------------------
-- Step 1 — dedupe on the new key (keep earliest row)
-- ---------------------------------------------------------------------------
delete from pending_applies a
  using pending_applies b
  where a.user_id is not distinct from b.user_id
    and a.pr_number = b.pr_number
    and a.scope = b.scope
    and (
      a.created_at > b.created_at
      or (a.created_at = b.created_at and a.id > b.id)
    );

-- ---------------------------------------------------------------------------
-- Step 2 — replace global (pr_number, scope) index
-- ---------------------------------------------------------------------------
drop index if exists pending_applies_pr_number_scope_key;

create unique index if not exists pending_applies_user_id_pr_number_scope_key
  on pending_applies (user_id, pr_number, scope);
