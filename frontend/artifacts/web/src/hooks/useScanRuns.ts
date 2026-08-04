import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { useAppConfig } from '@/api/config';
import { getSupabaseClient } from '@/api/supabaseClient';

export interface ScanRun {
  id: string;
  scope: string;
  unmanaged_flag: boolean;
  status: 'running' | 'complete' | 'failed';
  current_stage: string | null;
  started_at: string;
  completed_at: string | null;
  result_summary: {
    mode?: string;
    report_path?: string;
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
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;
  const enabled  = !!supabase && !!scope;

  return useQuery<ScanRun[]>({
    queryKey: ['scanRuns', scope],
    enabled,
    queryFn: async () => {
      const { data, error } = await supabase!
        .from('scan_runs')
        .select('*')
        .eq('scope', scope!)
        .order('started_at', { ascending: false })
        .limit(20);
      if (error) throw error;
      return (data ?? []) as ScanRun[];
    },
  });
}

export function useScanRun(runId: string | null) {
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;
  const enabled  = !!supabase && !!runId;

  return useQuery<ScanRun | null>({
    queryKey: ['scanRun', runId],
    enabled,
    // Poll every 3 s while the run is still in progress
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 3000 : false,
    queryFn: async () => {
      const { data, error } = await supabase!
        .from('scan_runs')
        .select('*')
        .eq('id', runId!)
        .single();
      if (error) throw error;
      return data as ScanRun;
    },
  });
}

/** Invalidates both the run detail and the history list for a given scope. */
export function useInvalidateScanRuns() {
  const qc = useQueryClient();
  return useCallback(
    (scope: string, runId?: string) => {
      qc.invalidateQueries({ queryKey: ['scanRuns', scope] });
      if (runId) qc.invalidateQueries({ queryKey: ['scanRun', runId] });
    },
    [qc],
  );
}
