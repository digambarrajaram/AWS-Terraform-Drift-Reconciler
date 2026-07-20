-- Add review-stage columns to drift_events so the PR review pipeline
-- can persist Trivy scan results and freshness-gate outcomes alongside
-- each drift row.

alter table drift_events
  add column if not exists trivy_passed              boolean,
  add column if not exists trivy_summary             jsonb,
  add column if not exists freshness_gate_status     text,
  add column if not exists freshness_gate_checked_at timestamptz;
