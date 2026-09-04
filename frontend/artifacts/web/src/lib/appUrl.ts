import type { AppConfig } from '@/api/config';

/** Canonical app base URL for auth email links (no trailing slash). */
export function appBaseUrl(config?: Pick<AppConfig, 'appUrl'> | null): string {
  const configured = config?.appUrl?.trim();
  if (configured) {
    return configured.replace(/\/$/, '');
  }
  const base = import.meta.env.BASE_URL || '/';
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  const pathPrefix = base === '/' ? '' : base.replace(/\/$/, '');
  return `${origin}${pathPrefix}`;
}

/** Build an absolute redirect URL for Supabase auth emails. */
export function authRedirectUrl(
  path: string,
  config?: Pick<AppConfig, 'appUrl'> | null,
): string {
  const normalized = path.replace(/^\//, '');
  return new URL(normalized, `${appBaseUrl(config)}/`).href;
}
