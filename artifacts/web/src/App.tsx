import { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';

import Layout from '@/components/layout/Layout';
import AuthPromptModal from '@/components/layout/AuthPromptModal';
import { useAuthStore } from '@/hooks/useAuthStore';
import { getToken } from '@/api/apiFetch';

import Overview from '@/pages/Overview';
import Scan from '@/pages/Scan';
import PrQueue from '@/pages/PrQueue';
import Rollback from '@/pages/Rollback';
import Trends from '@/pages/Trends';
import Exceptions from '@/pages/Exceptions';
import Alerts from '@/pages/Alerts';
import Environments from '@/pages/Environments';
import Explorer from '@/pages/Explorer';

const queryClient = new QueryClient();

/** Checks for a stored token on mount; if absent, prompts immediately. */
function AuthGuard({ children }: { children: React.ReactNode }) {
  const setNeedsToken = useAuthStore((s) => s.setNeedsToken);

  useEffect(() => {
    if (!getToken()) {
      setNeedsToken(true);
    }
  }, [setNeedsToken]);

  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <AuthGuard>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Overview />} />
              <Route path="/scan" element={<Scan />} />
              <Route path="/pr-queue" element={<PrQueue />} />
              <Route path="/rollback" element={<Rollback />} />
              <Route path="/trends" element={<Trends />} />
              <Route path="/exceptions" element={<Exceptions />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route path="/environments" element={<Environments />} />
              <Route path="/explorer" element={<Explorer />} />
            </Route>
          </Routes>
          <AuthPromptModal />
        </AuthGuard>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
