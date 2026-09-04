import { useAuthStore } from '@/hooks/useAuthStore';

export const TOKEN_KEY = 'drift_api_token';

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function saveToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// Module-level Supabase access token — synced by AuthProvider.
let supabaseAccessToken: string | null = null;

export function setSupabaseAccessToken(token: string | null): void {
  supabaseAccessToken = token;
}

export function getSupabaseAccessToken(): string | null {
  return supabaseAccessToken;
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  readonly status: number;
  readonly runId?: string;

  constructor(message: string, status: number, runId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.runId = runId;
  }
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

/**
 * Wraps fetch() for /api/* calls:
 *  - Prefixes path with /api
 *  - Injects Content-Type: application/json
 *  - Injects X-Api-Access-Token from localStorage (when present)
 *  - Injects Authorization: Bearer <supabase access token> (when present)
 *  - On 401: distinguishes JWT failure ("unauthorized") from API-token failure
 *  - On !ok: parses { error, run_id? } and throws ApiError
 *  - On success: returns parsed JSON as T
 */
export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const supabaseToken = getSupabaseAccessToken();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string> | undefined),
  };

  if (token) {
    headers['X-Api-Access-Token'] = token;
  }
  if (supabaseToken) {
    headers['Authorization'] = `Bearer ${supabaseToken}`;
  }

  const res = await fetch(`/api${path}`, { ...init, headers });

  if (res.status === 401) {
    let message = 'Unauthorized — token required or invalid';
    let jwtUnauthorized = false;
    try {
      const body = (await res.json()) as { error?: string };
      if (body.error === 'unauthorized') {
        jwtUnauthorized = true;
        message = 'Unauthorized — session required or invalid';
      } else if (body.error) {
        message = body.error;
      }
    } catch {
      // body is not JSON
    }

    if (!jwtUnauthorized) {
      clearToken();
      useAuthStore.getState().setNeedsToken(true);
    }

    throw new ApiError(message, 401);
  }

  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let runId: string | undefined;
    try {
      const body = (await res.json()) as { error?: string; run_id?: string };
      if (body.error) message = body.error;
      if (body.run_id) runId = body.run_id;
    } catch {
      // body is not JSON — use the status text fallback above
    }
    throw new ApiError(message, res.status, runId);
  }

  return res.json() as Promise<T>;
}
