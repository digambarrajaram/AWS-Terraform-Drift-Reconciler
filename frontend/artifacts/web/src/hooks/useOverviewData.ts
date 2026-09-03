import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/api/apiFetch';

const POLL_MS = 60_000;

type SeverityRow = { severity: string; count: number };
type OverviewPayload = {
  severity: SeverityRow[];
  rollback_count: number;
  last_scan: string | null;
  cost_impact: number;
};

/** Query keys for the Overview aggregate. */
export const overviewKeys = {
  all: (scope: string) => ['overview', scope] as const,
  severity: (scope: string) => ['overview', 'severity', scope] as const,
  rollback: (scope: string) => ['overview', 'rollback', scope] as const,
  lastScan: (scope: string) => ['overview', 'lastScan', scope] as const,
  cost:     (scope: string) => ['overview', 'cost',    scope] as const,
};

export function useOverviewData(scope: string | null) {
  const enabled = !!scope;

  const overview = useQuery<OverviewPayload>({
    queryKey: overviewKeys.all(scope ?? ''),
    enabled,
    refetchInterval: POLL_MS,
    queryFn: () => apiFetch<OverviewPayload>(`/overview?scope=${encodeURIComponent(scope!)}`),
  });

  const metric = <T,>(data: T | undefined) => ({
    data,
    error: overview.error,
    isLoading: overview.isLoading,
    isSuccess: overview.isSuccess,
  });
  return {
    severitySummary: metric(overview.data?.severity),
    rollbackCount: metric(overview.data?.rollback_count),
    lastScan: metric(overview.data?.last_scan ?? null),
    costImpact: metric(overview.data?.cost_impact),
  };
}
