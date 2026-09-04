import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/api/apiFetch';
import type { Environment } from '@/types';

// ── Query ──────────────────────────────────────────────────────────────────

async function fetchEnvironments(): Promise<Environment[]> {
  return apiFetch<Environment[]>('/environments');
}

export function useEnvironments() {
  const query = useQuery<Environment[]>({
    queryKey: ['environments'],
    queryFn: fetchEnvironments,
    staleTime: 30_000,
  });

  const allEnvironments    = query.data ?? [];
  const activeEnvironments = allEnvironments.filter((e) => e.is_active);

  return { ...query, allEnvironments, activeEnvironments };
}

// ── Mutations ──────────────────────────────────────────────────────────────

type EnvPayload = Partial<Omit<Environment, 'id' | 'is_active'>> & {
  slug?:                    string;
  _github_token?:           string;
};

export function useCreateEnvironment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EnvPayload) =>
      apiFetch<Environment>('/environments', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['environments'] }),
  });
}

export function useUpdateEnvironment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...payload }: EnvPayload & { id: string }) =>
      apiFetch<Environment>(`/environments/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['environments'] }),
  });
}

export function useDeleteEnvironment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/environments/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['environments'] }),
  });
}
