-- Distinguish review_only security PRs (no .tf patch) from real-fix
-- security PRs.  review_only merge still auto-excepts; real-fix merge
-- applies the patch with no exception and gains a separate Except action.
alter table pending_applies
  add column if not exists review_only boolean not null default false;

-- Refresh PostgREST schema cache so PGRST204 stops immediately.
notify pgrst, 'reload schema';
