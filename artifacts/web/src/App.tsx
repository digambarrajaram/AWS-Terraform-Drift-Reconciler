import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';

import Layout from '@/components/layout/Layout';
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

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
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
      </BrowserRouter>
    </QueryClientProvider>
  );
}
