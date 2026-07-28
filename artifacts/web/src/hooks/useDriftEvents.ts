import { useQuery } from '@tanstack/react-query';
import { keepPreviousData } from '@tanstack/react-query';
import { useAppConfig } from '@/api/config';
import { getSupabaseClient } from '@/api/supabaseClient';
import type { DriftEvent } from '@/types';

export type SortColumn = 'created_at' | 'severity' | 'resource_id';

export interface DriftFilters {
  statusFilter:   string; // 'open' | 'resolved' | 'suppressed' | 'all'
  severityFilter: string; // 'HIGH' | 'MEDIUM' | 'LOW' | 'all'
  typeFilter:     string; // 'fix' | 'batch' | 'rollback' | 'unmanaged' | 'manual' | 'all'
  search:         string; // resource_id ilike
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
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;
  const enabled  = !!supabase && !!scope;

  return useQuery<{ events: DriftEvent[]; count: number }>({
    queryKey: ['driftEvents', scope, filters, sort, page],
    enabled,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const offset = page * PAGE_SIZE;

      // Build query incrementally; each step returns the typed builder
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let q: any = supabase!
        .from('drift_events')
        .select('*', { count: 'exact' })
        .eq('account', scope!);

      if (filters.statusFilter   !== 'all') q = q.eq('status',   filters.statusFilter);
      if (filters.severityFilter !== 'all') q = q.eq('severity', filters.severityFilter);
      if (filters.typeFilter     !== 'all') q = q.eq('pr_type',  filters.typeFilter);
      if (filters.search)                   q = q.ilike('resource_id', `%${filters.search}%`);

      // NOTE: severity sort is alphabetical (H < L < M asc). A future
      // improvement is a custom severity_order column.
      q = q
        .order(sort.column, { ascending: sort.ascending })
        .range(offset, offset + PAGE_SIZE - 1);

      const { data, count, error } = await q;
      if (error) throw error;
      return { events: (data ?? []) as DriftEvent[], count: count ?? 0 };
    },
  });
}

export { PAGE_SIZE };
