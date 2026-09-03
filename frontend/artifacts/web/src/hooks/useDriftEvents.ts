import { useQuery } from '@tanstack/react-query';
import { keepPreviousData } from '@tanstack/react-query';
import { apiFetch } from '@/api/apiFetch';
import { normalizeDriftEvent } from '@/lib/drift';
import type { DriftEvent } from '@/types';

export type SortColumn = 'created_at' | 'severity' | 'resource_id';

export interface DriftFilters {
  statusFilter:   string; // 'open' | 'resolved' | 'suppressed' | 'all'
  severityFilter: string; // 'HIGH' | 'MEDIUM' | 'LOW' | 'all'
  typeFilter:     string; // 'fix' | 'batch' | 'rollback' | 'unmanaged' | 'security_only' | 'manual' | 'all'
  search:         string; // resource_id ilike
  // Optional date range — used by Explorer; PrQueue leaves these undefined
  dateFrom?: string;      // ISO date string, inclusive
  dateTo?:   string;      // ISO date string, inclusive (end of day applied server-side)
}

export interface DriftSort {
  column:    SortColumn;
  ascending: boolean;
}

const PAGE_SIZE = 20;

export function useDriftEvents(
  scope: string | null,
  filters: DriftFilters,
  sort: DriftSort,
  page: number,
) {
  const enabled = !!scope;

  return useQuery<{ events: DriftEvent[]; count: number }>({
    // Decompose filter/sort objects into primitives so the queryKey is immune
    // to object-reference changes — a new {status: 'open'} value means the
    // same cached query as an existing {status: 'open'} value.
    queryKey: [
      'driftEvents', scope,
      filters.statusFilter, filters.severityFilter, filters.typeFilter,
      filters.search, filters.dateFrom, filters.dateTo,
      sort.column, sort.ascending,
      page,
    ],
    enabled,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const query = new URLSearchParams({
        scope: scope!, page: String(page), sort: sort.column,
        ascending: String(sort.ascending), status: filters.statusFilter,
        severity: filters.severityFilter, type: filters.typeFilter,
      });
      if (filters.search) query.set('search', filters.search);
      if (filters.dateFrom) query.set('dateFrom', filters.dateFrom);
      if (filters.dateTo) query.set('dateTo', filters.dateTo);
      const result = await apiFetch<{ events: DriftEvent[]; count: number }>(`/pr-queue?${query}`);
      return { events: result.events.map(normalizeDriftEvent), count: result.count };
    },
  });
}

export { PAGE_SIZE };
