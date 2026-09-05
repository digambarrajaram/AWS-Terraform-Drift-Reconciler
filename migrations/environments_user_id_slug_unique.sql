-- Per-user environment slugs: replace global slug UNIQUE with (user_id, slug).
--
-- PREREQUISITE: migrations/add_user_id_ownership_columns.sql applied.
--
-- ---------------------------------------------------------------------------
-- Step 0 — pre-check (read-only; expect zero rows before applying)
-- ---------------------------------------------------------------------------
-- select user_id, slug, count(*) as n
-- from environments
-- group by user_id, slug
-- having count(*) > 1;
--
-- ---------------------------------------------------------------------------
-- Step 1 — drop global slug uniqueness
-- ---------------------------------------------------------------------------
alter table environments
  drop constraint if exists environments_slug_key;

-- ---------------------------------------------------------------------------
-- Step 2 — composite uniqueness per owner (NULL user_id rows remain distinct)
-- ---------------------------------------------------------------------------
create unique index if not exists environments_user_id_slug_key
  on environments (user_id, slug);

-- ---------------------------------------------------------------------------
-- Idempotent seed / upsert pattern (use after backfill assigns user_id):
--
--   insert into environments (user_id, slug, name, ...)
--   values ('<OWNER_USER_UUID>'::uuid, 'scope-a', 'Production A', ...)
--   on conflict (user_id, slug) do nothing;
--
-- Legacy bootstrap in create_environments_table.sql still uses on conflict (slug)
-- because user_id did not exist at table creation time.
