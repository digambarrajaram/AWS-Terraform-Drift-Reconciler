import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/api/apiFetch';
import type { Environment } from '@/types';

async function fetchEnvironments(): Promise<Environment[]> {
  return apiFetch<Environment[]>('/environments');
}

export function useEnvironments() {
  const query = useQuery<Environment[]>({
    queryKey: ['environments'],
    queryFn: fetchEnvironments,
    staleTime: 30_000,
  });

  const allEnvironments = query.data ?? [];
  const activeEnvironments = allEnvironments.filter((e) => e.is_active);

  return { ...query, allEnvironments, activeEnvironments };
}
