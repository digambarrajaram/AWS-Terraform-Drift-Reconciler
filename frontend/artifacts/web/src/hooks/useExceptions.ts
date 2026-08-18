import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/api/apiFetch';

// ── Types ──────────────────────────────────────────────────────────────────

export interface DriftException {
  id:               string | number;
  scope:            string;
  exception_type:   'drift';
  resource_address: string;
  drift_type:       string | null;
  reason:           string;
  approved_by:      string | null;
  expires:          string | null; // ISO date or null
  auto:             boolean;
  active:           boolean;
}

export interface UnmanagedException {
  id:                   string | number;
  scope:                string;
  exception_type:       'unmanaged';
  resource_type:        string;
  resource_id_pattern:  string;
  reason:               string;
  approved_by:          string | null;
  max_monthly_cost_usd: number | null;
  active:               boolean;
}

export interface SecurityException {
  id:               string | number;
  scope:            string;
  exception_type:   'security';
  resource_address: string;
  rule_id:          string;
  reason:           string;
  approved_by:      string | null;
  expires:          string | null;
  auto:             boolean;
  active:           boolean;
}

export interface ExceptionsResponse {
  drift_exceptions:     DriftException[];
  unmanaged_exceptions: UnmanagedException[];
  security_exceptions:  SecurityException[];
}

export interface ExceptionMutation {
  scope:           string;
  exception_type:  'drift' | 'unmanaged' | 'security';
  action:          'add' | 'expire' | 'delete';
  entry:           Record<string, unknown>;
}

// ── useExceptions ──────────────────────────────────────────────────────────

export function useExceptions(scope: string | null) {
  return useQuery<ExceptionsResponse>({
    queryKey: ['exceptions', scope],
    enabled:  !!scope,
    queryFn:  () => apiFetch<ExceptionsResponse>(`/exceptions?scope=${encodeURIComponent(scope!)}`),
  });
}

// ── useExceptionsMutation ──────────────────────────────────────────────────

export function useExceptionsMutation(scope: string | null) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: ExceptionMutation) =>
      apiFetch<unknown>('/exceptions', {
        method: 'POST',
        body:   JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['exceptions', scope] });
    },
  });
}
