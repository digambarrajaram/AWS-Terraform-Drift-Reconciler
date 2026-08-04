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
  const { data: config } = useAppConfig();
  const supabase = config ? getSupabaseClient(config) : null;
  const enabled  = !!supabase && !!scope;

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
      if (filters.dateFrom)                 q = q.gte('created_at', filters.dateFrom);
      if (filters.dateTo)                   q = q.lte('created_at', `${filters.dateTo}T23:59:59`);

      // When sorting by severity, skip the DB order (alphabetical H<L<M is wrong)
      // and apply a client-side comparator after the fetch instead.
      const sortBySeverityClient = sort.column === 'severity';
      if (!sortBySeverityClient) {
        q = q.order(sort.column, { ascending: sort.ascending });
      } else {
        // Stable secondary sort so pages are deterministic
        q = q.order('created_at', { ascending: false });
      }
      q = q.range(offset, offset + PAGE_SIZE - 1);

      const { data, count, error } = await q;
      if (error) throw error;

      let events = (data ?? []) as DriftEvent[];

      if (sortBySeverityClient) {
        const SEV_RANK: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };
        const rank = (s: string) => SEV_RANK[s.toUpperCase()] ?? 3;
        events = [...events].sort((a, b) => {
          const diff = rank(a.severity) - rank(b.severity);
          if (diff !== 0) return sort.ascending ? diff : -diff;
          // tie-break: newest first
          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        });
      }

      return { events, count: count ?? 0 };
    },
  });
}

export { PAGE_SIZE };
