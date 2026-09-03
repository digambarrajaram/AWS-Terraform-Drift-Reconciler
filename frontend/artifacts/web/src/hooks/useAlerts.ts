import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch, ApiError } from '@/api/apiFetch';

// ── Types ──────────────────────────────────────────────────────────────────

export type Severity = 'HIGH' | 'MEDIUM' | 'LOW';
export type Channel  = 'pagerduty' | 'slack' | 'none';

export interface NotificationSettings {
  pagerduty_configured: boolean;
  pagerduty_masked:     string | null;
  slack_configured:     boolean;
  slack_masked:         string | null;
  /** False when the backend returned 404 (endpoint not yet wired up). */
  backendAvailable:     boolean;
}

export interface RoutingRule {
  id:       string | number;
  severity: Severity;
  channel:  Channel;
  scope:    string | null; // null = global default
}

// ── useNotificationSettings ────────────────────────────────────────────────

const SETTINGS_DEFAULTS: NotificationSettings = {
  pagerduty_configured: false,
  pagerduty_masked:     null,
  slack_configured:     false,
  slack_masked:         null,
  backendAvailable:     false,
};

export function useNotificationSettings() {
  return useQuery<NotificationSettings>({
    queryKey: ['notificationSettings'],
    // Don't retry 404s — the endpoint simply isn't wired up yet.
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 2;
    },
    queryFn: async () => {
      try {
        const data = await apiFetch<Omit<NotificationSettings, 'backendAvailable'>>(
          '/notification-settings',
        );
        return { ...data, backendAvailable: true };
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          return SETTINGS_DEFAULTS; // render cards in "not configured" state
        }
        throw err; // surface real errors (auth, 5xx, network) normally
      }
    },
  });
}

export function useSaveCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ field, value }: { field: string; value: string }) =>
      apiFetch<{ success: boolean }>('/notification-settings', {
        method: 'POST',
        body:   JSON.stringify({ field, value }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notificationSettings'] }),
  });
}

export function useSendTest() {
  return useMutation({
    mutationFn: ({ channel, scope }: { channel: 'pagerduty' | 'slack'; scope?: string }) =>
      apiFetch<{ success: boolean }>('/notification-settings/test', {
        method: 'POST',
        body:   JSON.stringify({ channel, ...(scope ? { scope } : {}) }),
      }),
  });
}

// ── useRoutingRules ────────────────────────────────────────────────────────

export function useRoutingRules(scope: string | null) {
  return useQuery<RoutingRule[]>({
    queryKey: ['routingRules', scope],
    enabled: !!scope,
    queryFn: () => apiFetch<RoutingRule[]>(
      `/routing-rules?scope=${encodeURIComponent(scope!)}`,
    ),
  });
}

export function useSaveRoutingRule(scope: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rule: { severity: Severity; channel: Channel; scope?: string | null }) =>
      apiFetch<{ success: boolean }>('/routing-rules', {
        method: 'POST',
        body:   JSON.stringify(rule),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['routingRules', scope] }),
  });
}
