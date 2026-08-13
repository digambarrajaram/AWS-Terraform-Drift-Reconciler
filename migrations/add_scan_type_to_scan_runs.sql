-- Add scan_type column to scan_runs so the frontend and pipeline
-- can distinguish drift_only / drift_and_unmanaged / unmanaged_only /
-- trivy_only runs without parsing the result_summary JSONB blob.
--
-- NULL = legacy rows created before this column existed; those rows
-- were all drift_only or drift_and_unmanaged (the only modes the
-- pipeline supported at the time).

alter table scan_runs add column if not exists scan_type text;
-- values: 'drift_only' | 'drift_and_unmanaged' | 'unmanaged_only' | 'trivy_only'
