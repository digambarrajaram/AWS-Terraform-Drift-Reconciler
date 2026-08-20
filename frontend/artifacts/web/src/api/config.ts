import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './apiFetch';

export interface AppConfig {
  supabaseUrl: string;
  supabaseAnonKey: string;
  githubRepo?: string;  // e.g. "owner/repo" — used to build GitHub PR links
}

// apiFetch (not bare fetch) so the X-Api-Access-Token header is sent —
// serve.py auth-gates every /api/* route when API_ACCESS_TOKEN is set.
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
