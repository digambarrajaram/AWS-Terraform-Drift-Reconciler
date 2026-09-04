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
  clearServerSession,
  establishSession,
  setSupabaseAccessToken,
} from '@/api/apiFetch';
import {
  getSupabaseClient,
  signOut as supabaseSignOut,
} from '@/api/supabaseClient';

interface AuthContextValue {
  session: Session | null;
  user: User | null;
  loading: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function syncServerSession(accessToken: string | null): Promise<void> {
  if (!accessToken) return;
  setSupabaseAccessToken(accessToken);
  try {
    await establishSession();
  } catch {
    // Session cookie may already be valid, or login rate-limited — UI still works via JWT exchange retry.
  }
}

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
      setSession(null);
      setSupabaseAccessToken(null);
      setLoading(false);
      return;
    }

    const client = getSupabaseClient(config);
    let cancelled = false;

    client.auth.getSession().then(async ({ data }) => {
      if (cancelled) return;
      setSession(data.session);
      setSupabaseAccessToken(data.session?.access_token ?? null);
      if (data.session?.access_token) {
        await syncServerSession(data.session.access_token);
      }
      if (!cancelled) setLoading(false);
    });

    const {
      data: { subscription },
    } = client.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setSupabaseAccessToken(nextSession?.access_token ?? null);
      if (nextSession?.access_token) {
        void syncServerSession(nextSession.access_token);
      }
      setLoading(false);
    });

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, [config, configLoading]);

  const signOut = useCallback(async () => {
    await clearServerSession();
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
