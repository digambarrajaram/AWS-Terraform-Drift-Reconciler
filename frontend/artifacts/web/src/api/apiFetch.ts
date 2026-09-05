// Module-level Supabase access token — synced by AuthProvider.
let supabaseAccessToken: string | null = null;
let sessionSyncPromise: Promise<void> | null = null;
let lastSyncedToken: string | null = null;

export function setSupabaseAccessToken(token: string | null): void {
  supabaseAccessToken = token;
  if (!token) {
    lastSyncedToken = null;
    sessionSyncPromise = null;
  }
}

export function getSupabaseAccessToken(): string | null {
  return supabaseAccessToken;
}

function getCsrfToken(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(/(?:^|; )csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function establishSessionOnce(token: string): Promise<void> {
  await apiFetch<{ ok: boolean }>('/login', {
    method: 'POST',
    body: '{}',
  });
  lastSyncedToken = token;
}

/** Exchange Supabase JWT for HttpOnly session + CSRF cookies (deduped per token). */
export async function establishSession(): Promise<void> {
  const token = getSupabaseAccessToken();
  if (!token) return;
  if (token === lastSyncedToken) return;
  if (sessionSyncPromise) {
    await sessionSyncPromise;
    if (token === lastSyncedToken) return;
  }
  sessionSyncPromise = establishSessionOnce(token).finally(() => {
    sessionSyncPromise = null;
  });
  await sessionSyncPromise;
}

/** Clear server session cookies. */
export async function clearServerSession(): Promise<void> {
  try {
    await apiFetch<{ ok: boolean }>('/login', {
      method: 'POST',
      body: JSON.stringify({ logout: true }),
    });
  } catch {
    // Best-effort — local sign-out still proceeds.
  } finally {
    lastSyncedToken = null;
    sessionSyncPromise = null;
  }
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
 *  - Sends credentials (session + CSRF cookies)
 *  - Injects Authorization: Bearer <supabase access token> (for /api/login)
 *  - Injects X-CSRF-Token on state-changing methods (double-submit)
 *  - On 401: {"error":"unauthorized"}
 *  - On !ok: parses { error, run_id? } and throws ApiError
 *  - On success: returns parsed JSON as T
 */
export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const supabaseToken = getSupabaseAccessToken();
  const method = (init.method || 'GET').toUpperCase();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string> | undefined),
  };

  if (supabaseToken) {
    headers['Authorization'] = `Bearer ${supabaseToken}`;
  }

  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    const csrf = getCsrfToken();
    if (csrf) {
      headers['X-CSRF-Token'] = csrf;
    }
  }

  const res = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });

  if (res.status === 401) {
    let message = 'Unauthorized — session required or invalid';
    try {
      const body = (await res.json()) as { error?: string };
      if (body.error) {
        message = body.error === 'unauthorized'
          ? 'Unauthorized — session required or invalid'
          : body.error;
      }
    } catch {
      // body is not JSON
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

  const text = await res.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}
