import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { Session, User } from '@supabase/supabase-js';
import { useAppConfig } from '@/api/config';
import {
  getSupabaseClient,
  signOut as supabaseSignOut,
} from '@/api/supabaseClient';
import { setSupabaseAccessToken } from '@/api/apiFetch';

interface AuthContextValue {
  session: Session | null;
  user: User | null;
  loading: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { data: config, isLoading: configLoading } = useAppConfig();
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (configLoading) {
      setLoading(true);
      return;
    }

    if (!config) {
      // Config not available yet (e.g. API token still required).
      setSession(null);
      setSupabaseAccessToken(null);
      setLoading(false);
      return;
    }

    const client = getSupabaseClient(config);
    let cancelled = false;

    client.auth.getSession().then(({ data }) => {
      if (cancelled) return;
      setSession(data.session);
      setSupabaseAccessToken(data.session?.access_token ?? null);
      setLoading(false);
    });

    const {
      data: { subscription },
    } = client.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setSupabaseAccessToken(nextSession?.access_token ?? null);
      setLoading(false);
    });

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, [config, configLoading]);

  const signOut = useCallback(async () => {
    if (!config) {
      setSession(null);
      setSupabaseAccessToken(null);
      return;
    }
    await supabaseSignOut(config);
    setSession(null);
    setSupabaseAccessToken(null);
  }, [config]);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      user: session?.user ?? null,
      loading,
      signOut,
    }),
    [session, loading, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
