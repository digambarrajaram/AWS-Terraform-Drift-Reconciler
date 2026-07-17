create or replace view drift_severity_summary as
select
  account,
  severity,
  count(*) as count
from drift_events
where status = 'open'
group by account, severity
order by account, severity;
