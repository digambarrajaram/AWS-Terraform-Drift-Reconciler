import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/api/apiFetch';
import { normalizeDriftEvent } from '@/lib/drift';
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
  status:          'running' | 'complete' | 'failed' | 'cancelled';
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
  return useQuery<DriftEvent[]>({
    queryKey: ['eligiblePRs', scope],
    enabled: !!scope,
    queryFn: async () => {
      const result = await apiFetch<{ eligible: DriftEvent[] }>(
        `/rollback-data?scope=${encodeURIComponent(scope!)}`,
      );
      return result.eligible.map(normalizeDriftEvent);
    },
  });
}

// ── useRollbackRun ─────────────────────────────────────────────────────────

/**
 * Single rollback_run row. Polls every 3 s while status === 'running'.
 */
export function useRollbackRun(runId: string | null, scope: string | null) {

  return useQuery<RollbackRun | null>({
    queryKey: ['rollbackRun', runId],
    enabled: !!runId && !!scope,
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 3000 : false,
    queryFn: () => apiFetch<RollbackRun>(
      `/rollback-runs/${runId}?scope=${encodeURIComponent(scope!)}`,
    ),
  });
}

// ── useRollbackHistory ─────────────────────────────────────────────────────

export function useRollbackHistory(scope: string | null) {
  return useQuery<RollbackRun[]>({
    queryKey: ['rollbackHistory', scope],
    enabled: !!scope,
    queryFn: async () => {
      const result = await apiFetch<{ history: RollbackRun[] }>(
        `/rollback-data?scope=${encodeURIComponent(scope!)}`,
      );
      return result.history;
    },
  });
}
