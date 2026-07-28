import { createClient, SupabaseClient } from '@supabase/supabase-js';
import type { AppConfig } from './config';

let client: SupabaseClient | null = null;

/**
 * Returns a singleton Supabase client initialised with the runtime config.
 * Call only after useAppConfig() has resolved successfully.
 */
export function getSupabaseClient(config: AppConfig): SupabaseClient {
  if (!client) {
    client = createClient(config.supabaseUrl, config.supabaseAnonKey);
  }
  return client;
}

/** Clears the cached client (e.g. if config changes). */
export function resetSupabaseClient(): void {
  client = null;
}
