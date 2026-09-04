import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';

import { ErrorBoundary } from '@/components/ErrorBoundary';
import AppShell from '@/components/layout/AppShell';
import SessionGuard from '@/components/auth/SessionGuard';
import { Toaster } from '@/components/ui/sonner';
import { AuthProvider } from '@/hooks/useAuth';
import { ApiError } from '@/api/apiFetch';

import Overview from '@/pages/Overview';
import Scan from '@/pages/Scan';
import PrQueue from '@/pages/PrQueue';
import Approvals from '@/pages/Approvals';
import Rollback from '@/pages/Rollback';
import Trends from '@/pages/Trends';
import Exceptions from '@/pages/Exceptions';
import Alerts from '@/pages/Alerts';
import Environments from '@/pages/Environments';
import Explorer from '@/pages/Explorer';
import Login from '@/pages/Login';
import ResetPassword from '@/pages/ResetPassword';
import NotFound from '@/pages/NotFound';

// ── QueryClient ─────────────────────────────────────────────────────────────

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Suppress refetches on window focus during development — this is the
      // largest source of redundant requests when DevTools is open, since
      // every switch between DevTools and the page counts as a focus event.
      // Set to true (the RQ default) only if your users expect live data on
      // every tab switch.
      refetchOnWindowFocus: false,
      // Don't retry: auth errors, client errors, or endpoint-not-found.
      // Retry up to 2× for transient 5xx / network failures.
      retry: (failureCount, error) => {
        if (error instanceof ApiError) {
          if ([401, 403, 404, 409].includes(error.status)) return false;
          if (error.status >= 500) return failureCount < 2;
          return false;
        }
        return failureCount < 2;
      },
    },
    mutations: {
      // Mutations are never retried automatically.
      retry: false,
    },
  },
});

// ── App ─────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename={import.meta.env.BASE_URL}>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/reset-password" element={<ResetPassword />} />
              <Route element={<SessionGuard />}>
                <Route element={<AppShell />}>
                  <Route path="/"            element={<Overview />} />
                  <Route path="/scan"        element={<Scan />} />
                  <Route path="/pr-queue"    element={<PrQueue />} />
                  <Route path="/approvals"   element={<Approvals />} />
                  <Route path="/rollback"    element={<Rollback />} />
                  <Route path="/trends"      element={<Trends />} />
                  <Route path="/exceptions"  element={<Exceptions />} />
                  <Route path="/alerts"      element={<Alerts />} />
                  <Route path="/environments" element={<Environments />} />
                  <Route path="/explorer"    element={<Explorer />} />
                  {/* 404 — must be last inside AppShell so the shell still renders */}
                  <Route path="*"            element={<NotFound />} />
                </Route>
              </Route>
            </Routes>
            <Toaster richColors position="top-right" />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
