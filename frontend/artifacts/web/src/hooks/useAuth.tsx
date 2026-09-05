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
  /** True once POST /api/login has issued the server session cookie. */
  serverSessionReady: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { data: config, isLoading: configLoading } = useAppConfig();
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [serverSessionReady, setServerSessionReady] = useState(false);

  const syncServerSession = useCallback(async (accessToken: string | null) => {
    if (!accessToken) {
      setServerSessionReady(false);
      return;
    }
    setSupabaseAccessToken(accessToken);
    try {
      await establishSession();
      setServerSessionReady(true);
    } catch {
      setServerSessionReady(false);
    }
  }, []);

  useEffect(() => {
    if (configLoading) {
      setLoading(true);
      return;
    }

    if (!config) {
      setSession(null);
      setSupabaseAccessToken(null);
      setServerSessionReady(false);
      setLoading(false);
      return;
    }

    const client = getSupabaseClient(config);
    let cancelled = false;

    client.auth.getSession().then(async ({ data }) => {
      if (cancelled) return;
      setSession(data.session);
      if (data.session?.access_token) {
        await syncServerSession(data.session.access_token);
      } else {
        setServerSessionReady(false);
      }
      if (!cancelled) setLoading(false);
    });

    const {
      data: { subscription },
    } = client.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      if (nextSession?.access_token) {
        void syncServerSession(nextSession.access_token);
      } else {
        setSupabaseAccessToken(null);
        setServerSessionReady(false);
      }
      setLoading(false);
    });

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, [config, configLoading, syncServerSession]);

  const signOut = useCallback(async () => {
    await clearServerSession();
    if (!config) {
      setSession(null);
      setSupabaseAccessToken(null);
      setServerSessionReady(false);
      return;
    }
    await supabaseSignOut(config);
    setSession(null);
    setSupabaseAccessToken(null);
    setServerSessionReady(false);
  }, [config]);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      user: session?.user ?? null,
      loading,
      serverSessionReady,
      signOut,
    }),
    [session, loading, serverSessionReady, signOut],
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
