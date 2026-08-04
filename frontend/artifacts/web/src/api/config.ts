import { useQuery } from '@tanstack/react-query';

export interface AppConfig {
  supabaseUrl: string;
  supabaseAnonKey: string;
  githubRepo?: string;  // e.g. "owner/repo" — used to build GitHub PR links
}

async function fetchConfig(): Promise<AppConfig> {
  const res = await fetch('/api/config');
  if (!res.ok) throw new Error('Failed to load app config');
  return res.json() as Promise<AppConfig>;
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
