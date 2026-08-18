import { useCallback } from 'react';
import { format } from 'date-fns';
import { toast } from 'sonner';
import {
  CheckCircle, XCircle, Loader2, Inbox, AlertTriangle, ClipboardCheck,
} from 'lucide-react';

import { Skeleton } from '@/components/ui/skeleton';
import { apiFetch } from '@/api/apiFetch';
import { useScope } from '@/hooks/useScope';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { errorMessage } from '@/lib/errorUtils';

// ── Types ──────────────────────────────────────────────────────────────────

interface PendingApply {
  id: string;
  pr_number: number;
  scope: string;
  status: 'awaiting_approval' | 'approved' | 'rejected' | 'applied' | 'failed'
    | 'reverted_gate_blocked' | 'manual_revert_required';
  merged_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  applied_at: string | null;
  result: unknown;
  created_at: string;
}

const STATUS_STYLE: Record<PendingApply['status'], string> = {
  awaiting_approval:      'bg-amber-100  text-amber-700  dark:bg-amber-900/30 dark:text-amber-400',
  approved:               'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  rejected:               'bg-red-100    text-red-700    dark:bg-red-900/30 dark:text-red-400',
  applied:                'bg-blue-100   text-blue-700   dark:bg-blue-900/30  dark:text-blue-400',
  failed:                 'bg-zinc-100   text-zinc-600   dark:bg-zinc-800  dark:text-zinc-400',
  reverted_gate_blocked:  'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  manual_revert_required: 'bg-rose-100   text-rose-700   dark:bg-rose-900/30 dark:text-rose-400',
};

function fmtDate(iso: string | null) {
  if (!iso) return '—';
  try { return format(new Date(iso), 'MMM d, yyyy, HH:mm'); }
  catch { return '—'; }
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Approvals() {
  const { scope } = useScope();
  const queryClient = useQueryClient();

  const { data, isLoading, error, isFetching } = useQuery<PendingApply[]>({
    queryKey: ['pendingApplies', scope],
    enabled: !!scope,
    refetchInterval: 30_000,
    queryFn: () => apiFetch<PendingApply[]>(`/pending-applies?status=all${scope ? `&scope=${encodeURIComponent(scope)}` : ''}`),
  });

  const decide = useCallback(async (row: PendingApply, decision: 'approved' | 'rejected') => {
    try {
      await apiFetch(`/pending-applies/${row.id}/decision`, {
        method: 'POST',
        body: JSON.stringify({
          decision,
          // No per-user identity in this deployment — the token holder is the operator.
          approved_by: 'dashboard-user',
        }),
      });
      toast.success(decision === 'approved'
        ? `PR #${row.pr_number} approved`
        : `PR #${row.pr_number} rejected`);
      queryClient.invalidateQueries({ queryKey: ['pendingApplies'] });
    } catch (err) {
      toast.error('Failed to record decision', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }, [queryClient]);

  const rows = data ?? [];

  return (
    <div className="p-6 space-y-4 max-w-5xl">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <ClipboardCheck size={18} className="text-muted-foreground" />
          <h1 className="text-xl font-semibold">Approvals</h1>
        </div>
        {!isLoading && (
          <span className="text-xs text-muted-foreground">
            {rows.length} record{rows.length !== 1 ? 's' : ''}
            {isFetching && ' · updating…'}
          </span>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        Drift-fix PRs merged on GitHub land here for a human decision before any apply step runs.
      </p>

      {isLoading ? (
        <div className="space-y-2">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-4 text-sm text-destructive">
          <AlertTriangle size={15} className="shrink-0" />
          {errorMessage(error)}
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-border py-16 text-center">
          <Inbox size={36} className="text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">No pending applies for this scope</p>
        </div>
      ) : (
        <div className="rounded-xl border border-border overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[760px]">
              <thead>
                <tr className="border-b border-border bg-muted/40 text-left">
                  {['PR', 'Scope', 'Merged', 'Status', 'Decided By', 'Decided At', ''].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-xs font-medium text-muted-foreground whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => {
                  const style = STATUS_STYLE[row.status];
                  const awaiting = row.status === 'awaiting_approval';
                  return (
                    <tr key={row.id} className="transition-colors hover:bg-muted/30">
                      <td className="px-4 py-3 font-mono text-xs">#{row.pr_number}</td>
                      <td className="px-4 py-3 font-mono text-xs">{row.scope}</td>
                      <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
                        {fmtDate(row.merged_at)}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${style}`}>
                          {row.status.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">{row.approved_by ?? '—'}</td>
                      <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
                        {fmtDate(row.approved_at)}
                      </td>
                      <td className="px-4 py-3">
                        {awaiting && (
                          <div className="flex items-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => decide(row, 'approved')}
                              className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white transition-opacity hover:opacity-90"
                            >
                              <CheckCircle size={12} /> Approve
                            </button>
                            <button
                              type="button"
                              onClick={() => decide(row, 'rejected')}
                              className="inline-flex items-center gap-1 rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90"
                            >
                              <XCircle size={12} /> Reject
                            </button>
                          </div>
                        )}
                        {row.status === 'rejected' && <span className="text-xs text-muted-foreground">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
