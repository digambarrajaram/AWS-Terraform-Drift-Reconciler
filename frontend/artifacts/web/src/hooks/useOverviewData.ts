import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAppConfig } from '@/api/config';
import { getSupabaseClient } from '@/api/supabaseClient';

const POLL_MS = 60_000;

type SeverityRow = { severity: string; count: number };
type CostRow = { cost_impact: { monthly_estimate_usd?: number } | null };

/** All query keys for the overview so the realtime handler can invalidate them. */
export const overviewKeys = {
  severity: (scope: string) => ['overview', 'severity', scope] as const,
  rollback: (scope: string) => ['overview', 'rollback', scope] as const,
  lastScan: (scope: string) => ['overview', 'lastScan', scope] as const,
  cost:     (scope: string) => ['overview', 'cost',    scope] as const,
};

export function useOverviewData(scope: string | null) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;
  const enabled  = !!supabase && !!scope;

  // ── Severity breakdown ──────────────────────────────────────────────────
  const severitySummary = useQuery<SeverityRow[]>({
    queryKey: overviewKeys.severity(scope ?? ''),
    enabled,
    refetchInterval: POLL_MS,
    queryFn: async () => {
      const { data, error } = await supabase!
        .from('drift_severity_summary')
        .select('severity, count')
        .eq('account', scope!);
      if (error) throw error;
      return (data ?? []) as SeverityRow[];
    },
  });

  // ── Open rollback count ─────────────────────────────────────────────────
  const rollbackCount = useQuery<number>({
    queryKey: overviewKeys.rollback(scope ?? ''),
    enabled,
    refetchInterval: POLL_MS,
    queryFn: async () => {
      const { count, error } = await supabase!
        .from('drift_events')
        .select('*', { count: 'exact', head: true })
        .eq('status', 'open')
        .eq('pr_type', 'rollback')
        .eq('account', scope!);
      if (error) throw error;
      return count ?? 0;
    },
  });

  // ── Last scan timestamp ─────────────────────────────────────────────────
  const lastScan = useQuery<string | null>({
    queryKey: overviewKeys.lastScan(scope ?? ''),
    enabled,
    refetchInterval: POLL_MS,
    queryFn: async () => {
      const { data, error } = await supabase!
        .from('drift_events')
        .select('created_at')
        .eq('account', scope!)
        .order('created_at', { ascending: false })
        .limit(1);
      if (error) throw error;
      return data?.[0]?.created_at ?? null;
    },
  });

  // ── Cost impact ─────────────────────────────────────────────────────────
  const costImpact = useQuery<number>({
    queryKey: overviewKeys.cost(scope ?? ''),
    enabled,
    refetchInterval: POLL_MS,
    queryFn: async () => {
      const { data, error } = await supabase!
        .from('drift_events')
        .select('cost_impact')
        .eq('status', 'open')
        .eq('account', scope!);
      if (error) throw error;
      return ((data ?? []) as CostRow[]).reduce((sum, row) => {
        const est = row.cost_impact?.monthly_estimate_usd;
        return sum + (typeof est === 'number' ? est : 0);
      }, 0);
    },
  });

  // ── Realtime subscription ───────────────────────────────────────────────
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!supabase || !scope) return;

    function invalidateAll() {
      queryClient.invalidateQueries({ queryKey: overviewKeys.severity(scope!) });
      queryClient.invalidateQueries({ queryKey: overviewKeys.rollback(scope!) });
      queryClient.invalidateQueries({ queryKey: overviewKeys.lastScan(scope!) });
      queryClient.invalidateQueries({ queryKey: overviewKeys.cost(scope!) });
    }

    const channel = supabase
      .channel(`overview:${scope}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'drift_events',
          filter: `account=eq.${scope}`,
        },
        invalidateAll,
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'drift_events',
          filter: `account=eq.${scope}`,
        },
        invalidateAll,
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [supabase, scope, queryClient]);

  return { severitySummary, rollbackCount, lastScan, costImpact };
}
