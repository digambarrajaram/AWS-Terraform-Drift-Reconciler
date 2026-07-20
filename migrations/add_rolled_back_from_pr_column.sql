-- Track which PR a rollback was generated from, so the frontend can
-- link "fix → rolled back by PR #N" without inferring it from pr_type.

alter table drift_events
  add column if not exists rolled_back_from_pr integer;
