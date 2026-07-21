-- One-off backfill: mark pre-fix unmanaged-tracking PRs correctly.
-- Unmanaged findings use "drift-reports/" as their file_path (see
-- github_integration.py create_drift_pr_for_mode line 199), while
-- real drift fixes use the actual .tf file path.  This heuristic
-- catches all PRs created by the unmanaged-scan pipeline before the
-- unmanaged pr_type fix was applied.

-- Preview affected rows (safe to run first):
--   select id, resource_id, pr_number, pr_type, drift_summary
--   from drift_events
--   where file_path like 'drift-reports/%'
--     and pr_type = 'fix';

update drift_events
set pr_type = 'unmanaged',
    unmanaged = true
where file_path like 'drift-reports/%'
  and pr_type = 'fix';

-- Verify (should return 0 rows):
--   select count(*) from drift_events
--   where file_path like 'drift-reports/%' and pr_type != 'unmanaged';
