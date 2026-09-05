import type { Session } from '@supabase/supabase-js';
import type { AppConfig } from '@/api/config';
import { getSupabaseClient } from '@/api/supabaseClient';

/** True when the URL carries Supabase auth redirect params (hash or PKCE code). */
export function hasAuthRedirectInUrl(): boolean {
  if (typeof window === 'undefined') return false;
  const hash = window.location.hash.slice(1);
  if (hash.includes('access_token=')) return true;
  return new URLSearchParams(window.location.search).has('code');
}

/**
 * Parse Supabase email-link tokens from the URL and call setSession /
 * exchangeCodeForSession. Strips auth params from the address bar on success.
 */
export async function establishSessionFromAuthRedirect(
  config: AppConfig,
): Promise<Session | null> {
  if (!hasAuthRedirectInUrl()) return null;

  const client = getSupabaseClient(config);
  const search = new URLSearchParams(window.location.search);
  const code = search.get('code');

  if (code) {
    const { data, error } = await client.auth.exchangeCodeForSession(code);
    if (error) throw error;
    search.delete('code');
    const qs = search.toString();
    window.history.replaceState(
      null,
      '',
      window.location.pathname + (qs ? `?${qs}` : ''),
    );
    return data.session;
  }

  const hashParams = new URLSearchParams(window.location.hash.slice(1));
  const accessToken = hashParams.get('access_token');
  const refreshToken = hashParams.get('refresh_token');

  if (accessToken && refreshToken) {
    const { data, error } = await client.auth.setSession({
      access_token: accessToken,
      refresh_token: refreshToken,
    });
    if (error) throw error;
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
    return data.session;
  }

  return null;
}
