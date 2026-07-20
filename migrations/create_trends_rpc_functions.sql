-- RPC functions for drift-trends report — push aggregation to the
-- database so the Python script fetches only pre-computed summaries
-- instead of pulling raw rows client-side.
--
-- All functions are SECURITY INVOKER (the default) so the existing
-- anon_select_only RLS policy on drift_events still gates access.

-- 1. Most-drifted resources (top 15 by event count).
create or replace function get_most_drifted(
    p_account text,
    p_days    int default 90
)
returns table(resource_id text, drift_count bigint)
language sql
as $$
    select resource_id, count(*) as drift_count
    from drift_events
    where account = p_account
      and (p_days = 0 or created_at >= now() - (p_days || ' days')::interval)
    group by resource_id
    order by drift_count desc
    limit 15;
$$;

-- 2. Mean time to remediate by severity (fix/batch only, resolved only).
create or replace function get_mttr_by_severity(
    p_account text,
    p_days    int default 90
)
returns table(severity text, avg_hours numeric, count bigint)
language sql
as $$
    select severity,
           round(avg(extract(epoch from (resolved_at - created_at)) / 3600.0)::numeric, 1) as avg_hours,
           count(*)::bigint as count
    from drift_events
    where account = p_account
      and status = 'resolved'
      and pr_type in ('fix', 'batch')
      and resolved_at is not null
      and (p_days = 0 or created_at >= now() - (p_days || ' days')::interval)
    group by severity;
$$;

-- 3. Daily drift volume (events per day across statuses).
create or replace function get_drift_volume_daily(
    p_account text,
    p_days    int default 90
)
returns table(day date, count bigint)
language sql
as $$
    select date_trunc('day', created_at)::date as day,
           count(*)::bigint as count
    from drift_events
    where account = p_account
      and (p_days = 0 or created_at >= now() - (p_days || ' days')::interval)
    group by 1
    order by 1;
$$;

-- Grant execute to anon so the dashboard and trends script can call
-- these without the service-role key.
grant execute on function get_most_drifted(text, int) to anon;
grant execute on function get_mttr_by_severity(text, int) to anon;
grant execute on function get_drift_volume_daily(text, int) to anon;
