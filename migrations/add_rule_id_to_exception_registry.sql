-- Security exceptions need rule_id (e.g. AWS-0028 / AVD-AWS-0086) alongside
-- resource_address.  Without this column, Except / auto-add inserts fail and
-- the next Trivy scan cannot suppress findings by (resource, rule).
alter table drift_exception_registry
  add column if not exists rule_id text;
