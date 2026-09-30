import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/api/apiFetch';

const POLL_MS = 60_000;

type SeverityRow = { severity: string; count: number };

export type LastScanDrift = {
  count: number;
  found: boolean;
  skipped: boolean;
  reason: string | null;
  severity: SeverityRow[];
};

type OverviewPayload = {
  severity: SeverityRow[];
  open_count?: number;
  rollback_count: number;
  last_scan: string | null;
  last_scan_drift?: LastScanDrift;
  cost_impact: number;
  cost_resource_count?: number;
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

  // Disabled queries report isLoading=false — treat missing scope as loading
  // so cards don't flash zeros before the default scope is applied.
  const metric = <T,>(data: T | undefined) => ({
    data,
    error: overview.error,
    isLoading: !enabled || overview.isLoading,
    isSuccess: overview.isSuccess,
  });
  return {
    /** Unresolved open-ticket severity breakdown (status=open). */
    severitySummary: metric(overview.data?.severity),
    openCount: metric(
      overview.data?.open_count
        ?? (overview.data?.severity ?? []).reduce((n, r) => n + Number(r.count || 0), 0),
    ),
    /** Live findings from the latest completed scan — matches Scan History. */
    lastScanDrift: metric(overview.data?.last_scan_drift ?? {
      count: 0, found: false, skipped: false, reason: null, severity: [],
    }),
    rollbackCount: metric(overview.data?.rollback_count),
    lastScan: metric(overview.data?.last_scan ?? null),
    costImpact: metric(overview.data?.cost_impact),
    costResourceCount: metric(overview.data?.cost_resource_count ?? 0),
  };
}
