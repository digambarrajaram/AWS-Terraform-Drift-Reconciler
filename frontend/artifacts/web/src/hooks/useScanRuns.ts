import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { apiFetch } from '@/api/apiFetch';

export interface ScanRun {
  id: string;
  scope: string;
  unmanaged_flag: boolean;
  scan_type: string | null;
  status: 'running' | 'complete' | 'failed' | 'cancelled';
  current_stage: string | null;
  started_at: string;
  completed_at: string | null;
  result_summary: {
    mode?: string;
    report_path?: string;
    /** Present when status=failed — from humanize_terraform_error. */
    summary?: string;
    detail?: string;
    suggestion?: string;
    notice?: string;
    skipped_stages?: string[];
    drift?: {
      found: boolean;
      count: number;
      findings: unknown[];
      pr_links: string[];
    };
    unmanaged?: {
      found: boolean;
      count: number;
      findings: unknown[];
      pr_links: string[];
    };
    alerts_sent?: {
      pagerduty: number;
      slack: number;
    };
  } | null;
  pr_links: string[] | null;
}

export function useScanRunHistory(scope: string | null) {
  const enabled = !!scope;

  return useQuery<ScanRun[]>({
    queryKey: ['scanRuns', scope],
    enabled,
    queryFn: () => apiFetch<ScanRun[]>(`/scan-runs?scope=${encodeURIComponent(scope!)}`),
  });
}

export function useScanRun(runId: string | null, scope: string | null) {
  const enabled = !!runId && !!scope;

  return useQuery<ScanRun | null>({
    queryKey: ['scanRun', runId, scope],
    enabled,
    // Poll every 3 s while the run is still in progress
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 3000 : false,
    queryFn: () => apiFetch<ScanRun>(
      `/scan-runs/${runId}?scope=${encodeURIComponent(scope!)}`,
    ),
  });
}

/** Invalidates both the run detail and the history list for a given scope. */
export function useInvalidateScanRuns() {
  const qc = useQueryClient();
  return useCallback(
    (scope: string, runId?: string) => {
      qc.invalidateQueries({ queryKey: ['scanRuns', scope] });
      if (runId) qc.invalidateQueries({ queryKey: ['scanRun', runId, scope] });
    },
    [qc],
  );
}
