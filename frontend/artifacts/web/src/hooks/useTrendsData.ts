import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/api/apiFetch';

// ── Types ──────────────────────────────────────────────────────────────────

export interface MostDriftedRow { resource_id: string; drift_count: number; }

export interface MTTRRow { severity: string; avg_hours: number; count: number; }

export interface VolumeRow { day: string; count: number; }

export interface DriftSummary { total: number; uniqueResources: number; resolved: number; open: number; rollback: number; }

// ── Shared helpers ─────────────────────────────────────────────────────────

interface TrendsPayload {
  most_drifted: MostDriftedRow[];
  mttr: MTTRRow[];
  volume: VolumeRow[];
  summary: DriftSummary;
}

function useTrendsData(scope: string | null, days: number) {
  return useQuery<TrendsPayload>({
    queryKey: ['trends', scope, days],
    enabled: !!scope,
    queryFn: () => apiFetch<TrendsPayload>(
      `/trends?scope=${encodeURIComponent(scope!)}&days=${days}`,
    ),
  });
}

// ── useMostDrifted ─────────────────────────────────────────────────────────

export function useMostDrifted(scope: string | null, days: number) {
  const query = useTrendsData(scope, days);
  return { ...query, data: query.data?.most_drifted };
}

// ── useMTTRBySeverity ──────────────────────────────────────────────────────

export function useMTTRBySeverity(scope: string | null, days: number) {
  const query = useTrendsData(scope, days);
  return { ...query, data: query.data?.mttr };
}

// ── useDriftVolumeDaily ────────────────────────────────────────────────────

export function useDriftVolumeDaily(scope: string | null, days: number) {
  const query = useTrendsData(scope, days);
  return { ...query, data: query.data?.volume };
}

// ── useDriftSummary ────────────────────────────────────────────────────────

export function useDriftSummary(scope: string | null, days: number) {
  const query = useTrendsData(scope, days);
  return { ...query, data: query.data?.summary };
}
