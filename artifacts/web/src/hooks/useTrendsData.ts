import { useQuery } from '@tanstack/react-query';
import { useAppConfig } from '@/api/config';
import { getSupabaseClient } from '@/api/supabaseClient';

// ── Types ──────────────────────────────────────────────────────────────────

export interface MostDriftedRow {
  resource_id: string;
  drift_count:  number;
}

export interface MTTRRow {
  severity:  string;
  avg_hours: number;
  count:     number;
}

export interface VolumeRow {
  day:   string; // ISO date string
  count: number;
}

export interface DriftSummary {
  total:           number;
  uniqueResources: number;
  resolved:        number;
  open:            number;
  rollback:        number;
}

// ── Shared helpers ─────────────────────────────────────────────────────────

function sinceIso(days: number) {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

// ── useMostDrifted ─────────────────────────────────────────────────────────

export function useMostDrifted(scope: string | null, days: number) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;

  return useQuery<MostDriftedRow[]>({
    queryKey: ['mostDrifted', scope, days],
    enabled:  !!supabase && !!scope,
    queryFn: async () => {
      const { data, error } = await supabase!.rpc('get_most_drifted', {
        p_account: scope!,
        p_days:    days,
      });
      if (error) throw error;
      return (data ?? []) as MostDriftedRow[];
    },
  });
}

// ── useMTTRBySeverity ──────────────────────────────────────────────────────

export function useMTTRBySeverity(scope: string | null, days: number) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;

  return useQuery<MTTRRow[]>({
    queryKey: ['mttrBySeverity', scope, days],
    enabled:  !!supabase && !!scope,
    queryFn: async () => {
      const { data, error } = await supabase!.rpc('get_mttr_by_severity', {
        p_account: scope!,
        p_days:    days,
      });
      if (error) throw error;
      return (data ?? []) as MTTRRow[];
    },
  });
}

// ── useDriftVolumeDaily ────────────────────────────────────────────────────

export function useDriftVolumeDaily(scope: string | null, days: number) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;

  return useQuery<VolumeRow[]>({
    queryKey: ['driftVolumeDaily', scope, days],
    enabled:  !!supabase && !!scope,
    queryFn: async () => {
      const { data, error } = await supabase!.rpc('get_drift_volume_daily', {
        p_account: scope!,
        p_days:    days,
      });
      if (error) throw error;
      return (data ?? []) as VolumeRow[];
    },
  });
}

// ── useDriftSummary ────────────────────────────────────────────────────────

export function useDriftSummary(scope: string | null, days: number) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;

  return useQuery<DriftSummary>({
    queryKey: ['driftSummary', scope, days],
    enabled:  !!supabase && !!scope,
    queryFn: async () => {
      const since = sinceIso(days);
      // Helper that starts a count-only query already scoped to account + window
      const countQ = () =>
        supabase!.from('drift_events')
          .select('*', { count: 'exact', head: true })
          .eq('account', scope!)
          .gte('created_at', since);

      const [total, resolved, open, rollback, resources] = await Promise.all([
        countQ(),
        countQ().eq('status', 'resolved'),
        countQ().eq('status', 'open'),
        countQ().eq('pr_type', 'rollback'),
        supabase!.from('drift_events')
          .select('resource_id')
          .eq('account', scope!)
          .gte('created_at', since),
      ]);

      // Throw on the first error encountered
      for (const res of [total, resolved, open, rollback, resources]) {
        if (res.error) throw res.error;
      }

      const uniqueResources = new Set(
        (resources.data ?? []).map((r: { resource_id: string }) => r.resource_id),
      ).size;

      return {
        total:           total.count    ?? 0,
        resolved:        resolved.count ?? 0,
        open:            open.count     ?? 0,
        rollback:        rollback.count ?? 0,
        uniqueResources,
      };
    },
  });
}
