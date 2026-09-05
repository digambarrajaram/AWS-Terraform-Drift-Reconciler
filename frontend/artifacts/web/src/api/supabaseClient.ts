import { createClient, SupabaseClient, type Session, type User } from '@supabase/supabase-js';
import type { AppConfig } from './config';

let client: SupabaseClient | null = null;

/**
 * Returns a singleton Supabase client initialised with the runtime config.
 * Call only after useAppConfig() has resolved successfully.
 */
export function getSupabaseClient(config: AppConfig): SupabaseClient {
  if (!client) {
    client = createClient(config.supabaseUrl, config.supabaseAnonKey, {
      auth: {
        detectSessionInUrl: true,
        persistSession: true,
        autoRefreshToken: true,
      },
    });
  }
  return client;
}

/** Clears the cached client (e.g. if config changes). */
export function resetSupabaseClient(): void {
  client = null;
}

/** Require an already-initialised singleton client. */
function requireClient(config: AppConfig): SupabaseClient {
  return getSupabaseClient(config);
}

export async function signIn(
  config: AppConfig,
  email: string,
  password: string,
) {
  return requireClient(config).auth.signInWithPassword({ email, password });
}

export async function signUp(
  config: AppConfig,
  email: string,
  password: string,
  emailRedirectTo: string,
) {
  return requireClient(config).auth.signUp({
    email,
    password,
    options: { emailRedirectTo },
  });
}

export async function signOut(config: AppConfig) {
  return requireClient(config).auth.signOut();
}

export async function resetPasswordForEmail(
  config: AppConfig,
  email: string,
  redirectTo: string,
) {
  return requireClient(config).auth.resetPasswordForEmail(email, { redirectTo });
}

export async function resendSignupConfirmation(
  config: AppConfig,
  email: string,
  emailRedirectTo: string,
) {
  return requireClient(config).auth.resend({
    type: 'signup',
    email,
    options: { emailRedirectTo },
  });
}

export async function updatePassword(config: AppConfig, password: string) {
  return requireClient(config).auth.updateUser({ password });
}

export async function getSession(config: AppConfig): Promise<Session | null> {
  const { data, error } = await requireClient(config).auth.getSession();
  if (error) throw error;
  return data.session;
}

export type { Session, User };
