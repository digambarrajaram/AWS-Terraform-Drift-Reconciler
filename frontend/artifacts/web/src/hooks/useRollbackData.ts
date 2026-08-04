import { useQuery } from '@tanstack/react-query';
import { useAppConfig } from '@/api/config';
import { getSupabaseClient } from '@/api/supabaseClient';
import type { DriftEvent } from '@/types';

// ── Types ──────────────────────────────────────────────────────────────────

export interface PreviewDiffRow {
  resource_id: string;
  field:        string;
  original:     unknown;
  fixed:        unknown;
  current_live: unknown;
}

export interface RollbackRun {
  id:              string;
  pr_number:       number;
  scope:           string;
  mode:            'preview' | 'execute';
  status:          'running' | 'complete' | 'failed';
  current_stage:   string | null;
  started_at:      string;
  completed_at:    string | null;
  result:          {
    // success shapes
    diff?: PreviewDiffRow[];
    pr_url?: string;
    // failure shapes (from humanize_rollback_error)
    summary?:    string;
    detail?:     string;
    suggestion?: string;
  } | null;
  rollback_pr_url: string | null;
}

// ── useEligiblePRs ─────────────────────────────────────────────────────────

/**
 * Open drift_events for the scope — these are the rollback candidates.
 * Filters client-side to only rows with a pr_number.
 */
export function useEligiblePRs(scope: string | null) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;

  return useQuery<DriftEvent[]>({
    queryKey: ['eligiblePRs', scope],
    enabled:  !!supabase && !!scope,
    queryFn: async () => {
      const { data, error } = await supabase!
        .from('drift_events')
        .select('*')
        .eq('account', scope!)
        .eq('status', 'open')
        .order('created_at', { ascending: false });
      if (error) throw error;
      return (data ?? []).filter((e: DriftEvent) => e.pr_number != null) as DriftEvent[];
    },
  });
}

// ── useRollbackRun ─────────────────────────────────────────────────────────

/**
 * Single rollback_run row. Polls every 3 s while status === 'running'.
 */
export function useRollbackRun(runId: string | null) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;

  return useQuery<RollbackRun | null>({
    queryKey: ['rollbackRun', runId],
    enabled:  !!supabase && !!runId,
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 3000 : false,
    queryFn: async () => {
      const { data, error } = await supabase!
        .from('rollback_runs')
        .select('*')
        .eq('id', runId!)
        .single();
      if (error) throw error;
      return data as RollbackRun | null;
    },
  });
}

// ── useRollbackHistory ─────────────────────────────────────────────────────

export function useRollbackHistory(scope: string | null) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;

  return useQuery<RollbackRun[]>({
    queryKey: ['rollbackHistory', scope],
    enabled:  !!supabase && !!scope,
    queryFn: async () => {
      const { data, error } = await supabase!
        .from('rollback_runs')
        .select('*')
        .eq('scope', scope!)
        .order('started_at', { ascending: false });
      if (error) throw error;
      return (data ?? []) as RollbackRun[];
    },
  });
}
