-- Exclude unmanaged (and security-only) findings from drift trend
-- aggregations so "Most Drifted Resources" and volume charts reflect
-- configuration drift only.

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
      and coalesce(unmanaged, false) = false
      and coalesce(pr_type, 'fix') not in ('unmanaged', 'security_only')
      and (p_days = 0 or created_at >= current_date - ((p_days - 1) || ' days')::interval)
    group by resource_id
    order by drift_count desc
    limit 15;
$$;

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
      and coalesce(unmanaged, false) = false
      and coalesce(pr_type, 'fix') not in ('unmanaged', 'security_only')
      and (p_days = 0 or created_at >= current_date - ((p_days - 1) || ' days')::interval)
    group by 1
    order by 1;
$$;
