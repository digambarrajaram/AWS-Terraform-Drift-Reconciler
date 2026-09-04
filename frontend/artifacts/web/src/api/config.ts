import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './apiFetch';

export interface AppConfig {
  supabaseUrl: string;
  supabaseAnonKey: string;
  githubRepo?: string;  // e.g. "owner/repo" — used to build GitHub PR links
}

// apiFetch (not bare fetch) so credentials (session cookie) are sent —
// serve.py auth-gates every /api/* route except the public allowlist.
async function fetchConfig(): Promise<AppConfig> {
  return apiFetch<AppConfig>('/config');
}

/** Fetches /api/config once on app boot. Result is cached indefinitely. */
export function useAppConfig() {
  return useQuery<AppConfig>({
    queryKey: ['appConfig'],
    queryFn: fetchConfig,
    staleTime: Infinity,
    retry: 2,
  });
}
