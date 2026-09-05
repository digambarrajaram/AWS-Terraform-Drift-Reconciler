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
import {
  establishSessionFromAuthRedirect,
  hasAuthRedirectInUrl,
} from '@/lib/supabaseAuthRedirect';

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

    (async () => {
      // Email links land with #access_token=...&type=recovery (or ?code= for PKCE).
      // Parse explicitly before getSession so /reset-password does not dead-end.
      if (hasAuthRedirectInUrl()) {
        try {
          const redirected = await establishSessionFromAuthRedirect(config);
          if (cancelled) return;
          if (redirected) {
            setSession(redirected);
            await syncServerSession(redirected.access_token);
            setLoading(false);
            return;
          }
        } catch {
          if (cancelled) return;
          setSession(null);
          setServerSessionReady(false);
          setLoading(false);
          return;
        }
      }

      const { data } = await client.auth.getSession();
      if (cancelled) return;
      setSession(data.session);
      if (data.session?.access_token) {
        await syncServerSession(data.session.access_token);
      } else {
        setServerSessionReady(false);
      }
      setLoading(false);
    })();

    const {
      data: { subscription },
    } = client.auth.onAuthStateChange((event, nextSession) => {
      setSession(nextSession);
      if (nextSession?.access_token) {
        void syncServerSession(nextSession.access_token);
      } else {
        setSupabaseAccessToken(null);
        setServerSessionReady(false);
      }
      if (event === 'PASSWORD_RECOVERY' || event === 'SIGNED_IN') {
        setLoading(false);
      }
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
