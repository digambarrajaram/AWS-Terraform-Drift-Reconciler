import { ApiError } from '@/api/apiFetch';

/**
 * Returns a human-readable error message that distinguishes:
 *  - 502 / 503 / 504 → "Database unreachable — please retry"
 *  - 401             → "Authentication required" (apiFetch re-prompts automatically)
 *  - 404             → "Endpoint not found (backend may not be configured)"
 *  - other ApiError  → the message from the server
 *  - network errors  → "Network unreachable — please retry"
 *  - anything else   → the error's .message
 */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 502 || err.status === 503 || err.status === 504) {
      return 'Database unreachable — please retry';
    }
    if (err.status === 401) {
      return 'Session expired — please sign in again';
    }
    if (err.status === 429) {
      return 'Too many requests — please wait a moment and try again';
    }
    if (err.status === 404) {
      return 'Endpoint not found (backend may not be configured)';
    }
    return err.message || `Server error (${err.status})`;
  }
  const msg = err instanceof Error ? err.message : String(err);
  // Supabase / fetch network failures don't have a status code
  if (/fetch|network|connection|ECONNREFUSED|timeout|unreachable/i.test(msg)) {
    return 'Network unreachable — please retry';
  }
  return msg;
}

/**
 * True for transient errors the user can fix by retrying (5xx, network).
 */
export function isRetryable(err: unknown): boolean {
  if (err instanceof ApiError) {
    return err.status >= 500;
  }
  const msg = err instanceof Error ? err.message : String(err);
  return /fetch|network|connection|timeout|unreachable/i.test(msg);
}
