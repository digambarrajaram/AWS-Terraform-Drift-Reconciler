import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';

/**
 * Requires a Supabase Auth session and a server session cookie from POST /api/login.
 */
export default function SessionGuard() {
  const { session, loading, serverSessionReady } = useAuth();
  const location = useLocation();

  if (loading || (session && !serverSessionReady)) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">
        Signing in…
      </div>
    );
  }

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
