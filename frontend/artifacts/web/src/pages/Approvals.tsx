import { useCallback, useState } from 'react';
import { format } from 'date-fns';
import { toast } from 'sonner';
import {
  CheckCircle, XCircle, Inbox, AlertTriangle, ClipboardCheck,
  ExternalLink, FileDiff, GitCommit, ShieldCheck, Ban, Loader2,
} from 'lucide-react';

import { Skeleton } from '@/components/ui/skeleton';
import { LogViewer } from '@/components/shared/LogViewer';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { apiFetch } from '@/api/apiFetch';
import { useScope } from '@/hooks/useScope';
import { useScanLogs } from '@/hooks/useScanLogs';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { errorMessage } from '@/lib/errorUtils';

// ── Types ──────────────────────────────────────────────────────────────────

interface PendingApply {
  id: string;
  pr_number: number;
  scope: string;
  status: 'awaiting_approval' | 'approved' | 'rejected' | 'applied' | 'failed'
    | 'cancelled' | 'reverted_gate_blocked' | 'manual_revert_required';
  merged_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  applied_at: string | null;
  result: unknown;
  created_at: string;
}

interface PrDetails {
  number: number;
  title: string;
  body: string;
  state: string;
  merged: boolean;
  mergeable: boolean | null;
  mergeable_state: string;
  additions: number;
  deletions: number;
  changed_files: number;
  commits: { sha: string; message: string }[];
  files: { name: string; additions: number; deletions: number; status: string }[];
  checks: { name: string; conclusion: string }[];
  html_url: string;
}

const STATUS_STYLE: Record<PendingApply['status'], string> = {
  awaiting_approval:      'bg-amber-100  text-amber-700  dark:bg-amber-900/30 dark:text-amber-400',
  approved:               'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  rejected:               'bg-red-100    text-red-700    dark:bg-red-900/30 dark:text-red-400',
  applied:                'bg-blue-100   text-blue-700   dark:bg-blue-900/30  dark:text-blue-400',
  failed:                 'bg-zinc-100   text-zinc-600   dark:bg-zinc-800  dark:text-zinc-400',
  cancelled:              'bg-slate-100  text-slate-700  dark:bg-slate-900/30 dark:text-slate-400',
  reverted_gate_blocked:  'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  manual_revert_required: 'bg-rose-100   text-rose-700   dark:bg-rose-900/30 dark:text-rose-400',
};

function fmtDate(iso: string | null) {
  if (!iso) return '—';
  try { return format(new Date(iso), 'MMM d, yyyy, HH:mm'); }
  catch { return '—'; }
}

// Minimal markdown renderer for the PR body — only what this project's
// own PR templates emit: headings, bold, bullets, and fenced code blocks.
// ponytail: no react-markdown dep for one fixed template shape; expand if
// PR bodies ever come from external authors.
function MarkdownBody({ body }: { body: string }) {
  const parts = body.split(/```(?:text|json|hcl|diff)?\s*\n?([\s\S]*?)```/g);
  const blocks: React.ReactNode[] = [];
  parts.forEach((part, i) => {
    if (part === undefined) return;
    if (i % 2 === 1) {
      // odd index = fenced code content
      blocks.push(
        <details key={i} className="rounded-md border border-border bg-muted/40 my-2">
          <summary className="cursor-pointer px-3 py-1.5 text-[11px] font-mono text-muted-foreground select-none">
            code block
          </summary>
          <pre className="whitespace-pre-wrap break-all px-3 pb-3 text-xs font-mono text-foreground overflow-x-auto">
            {part}
          </pre>
        </details>,
      );
    } else if (part) {
      blocks.push(
        <div key={i} className="text-xs text-foreground/80 leading-relaxed whitespace-pre-wrap">
          {part.split('\n').map((line, j) => (
            <p key={j} className={line.startsWith('### ') ? 'font-semibold text-foreground mt-2' : ''}>
              {line || ' '}
            </p>
          ))}
        </div>,
      );
    }
  });
  return <div>{blocks}</div>;
}

// ── Detail drawer ──────────────────────────────────────────────────────────

function DetailDrawer({
  row, onClose, onDecide,
}: {
  row: PendingApply | null;
  onClose: () => void;
  onDecide: (row: PendingApply, decision: 'approved' | 'rejected') => void;
}) {
  const open = !!row;

  const { data: details, isLoading: detailsLoading } = useQuery<PrDetails>({
    queryKey: ['prDetails', row?.id],
    enabled: !!row,
    queryFn: () => apiFetch<PrDetails>(`/pending-applies/${row!.id}/pr-details`),
  });

  // Poll the live row while the drawer is open — the list query only
  // refetches every 30s, which made the badge look stuck after a decision.
  const { data: liveRow } = useQuery<PendingApply>({
    queryKey: ['pendingApply', row?.id],
    enabled: !!row,
    refetchInterval: 3000,
    queryFn: () => apiFetch<PendingApply>(`/pending-applies/${row!.id}`),
  });
  const current = liveRow ?? row;

  // Apply/revert job logs are keyed by the pending row id.  Keep the id
  // even after the status turns terminal (applied/failed/…) so the final
  // logs persist in the drawer — nulling it there would reset the hook's
  // accumulated lines exactly when "Job finished" is about to render.
  // Only awaiting_approval polls nothing (no job has ever run for it).
  const { lines, complete } = useScanLogs(
    current && current.status !== 'awaiting_approval' ? current.id : null,
  );

  // Backend run state for the indicator:
  //   approved/rejected  → job spawned, still running
  //   applied/failed/manual_revert_required/reverted_gate_blocked → done
  //   cancelled → stopped by user
  const jobRunning = current && ['approved', 'rejected'].includes(current.status);
  const jobDone = current && ['applied', 'failed', 'manual_revert_required',
    'reverted_gate_blocked', 'cancelled'].includes(current.status);

  const [cancelling, setCancelling] = useState(false);
  const cancel = useCallback(async () => {
    if (!row) return;
    setCancelling(true);
    try {
      await apiFetch(`/pending-applies/${row.id}/cancel`, { method: 'POST' });
      toast.success('Apply cancelled');
    } catch (err) {
      toast.error('Failed to cancel', {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setCancelling(false);
    }
  }, [row]);

  // Guard after ALL hooks (Rules of Hooks) — only the JSX return is skipped.
  if (!current) return null;

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
        {row && (
          <>
            <SheetHeader className="mb-4">
              <SheetTitle className="text-base break-all flex items-center gap-2">
                PR #{row.pr_number}
                <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[current.status]}`}>
                  {current.status.replace(/_/g, ' ')}
                </span>
              </SheetTitle>
            </SheetHeader>

            {/* ── Job run indicator ── */}
            {jobRunning && (
              <div className="mb-4 flex items-center gap-2 rounded-lg border border-amber-400/40 bg-amber-100/50 dark:bg-amber-900/20 px-3 py-2">
                <Loader2 size={14} className="animate-spin text-amber-600 dark:text-amber-400" />
                <span className="text-xs text-amber-800 dark:text-amber-300">
                  {current.status === 'approved' ? 'Applying accepted drift…' : 'Reverting drift…'} — job in progress
                </span>
              </div>
            )}
            {jobDone && (
              <div className="mb-4 flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
                {current.status === 'applied'
                  ? <CheckCircle size={14} className="text-emerald-500" />
                  : <XCircle size={14} className="text-destructive" />}
                <span className="text-xs text-muted-foreground">
                  Job finished: {current.status.replace(/_/g, ' ')}
                </span>
              </div>
            )}

            {/* ── PR details from GitHub ── */}
            {detailsLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-1/2" />
              </div>
            ) : details ? (
              <div className="space-y-4 text-sm">
                <div>
                  <p className="text-foreground font-medium">{details.title}</p>
                  <a href={details.html_url} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline mt-1">
                    <ExternalLink size={11} /> View on GitHub
                  </a>
                </div>

                {/* Mergeability / conflicts */}
                <div className="flex flex-wrap gap-2">
                  <span className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs">
                    {details.mergeable === false
                      ? <XCircle size={11} className="text-destructive" />
                      : <CheckCircle size={11} className="text-emerald-500" />}
                    {details.mergeable === null ? 'mergeable: unknown' : details.mergeable ? 'mergeable' : 'conflicts present'}
                  </span>
                  <span className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground">
                    state: {details.mergeable_state}
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs">
                    <FileDiff size={11} /> +{details.additions} −{details.deletions} in {details.changed_files} file(s)
                  </span>
                </div>

                {/* Checks */}
                {details.checks.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Checks</p>
                    {details.checks.map((c) => (
                      <div key={c.name} className="flex items-center gap-2 text-xs">
                        {c.conclusion === 'success'
                          ? <CheckCircle size={11} className="text-emerald-500" />
                          : c.conclusion === 'failure'
                            ? <XCircle size={11} className="text-destructive" />
                            : <ShieldCheck size={11} className="text-amber-500" />}
                        {c.name} · {c.conclusion ?? 'pending'}
                      </div>
                    ))}
                  </div>
                )}

                {/* Body */}
                {details.body && (
                  <div>
                    <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Description</p>
                    <MarkdownBody body={details.body} />
                  </div>
                )}

                {/* Commits */}
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1 flex items-center gap-1">
                    <GitCommit size={11} /> Commits ({details.commits.length})
                  </p>
                  <div className="space-y-1">
                    {details.commits.map((c) => (
                      <p key={c.sha} className="text-xs font-mono text-muted-foreground truncate">
                        {c.sha} {c.message}
                      </p>
                    ))}
                  </div>
                </div>

                {/* Files */}
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Files changed</p>
                  <div className="space-y-1">
                    {details.files.map((f) => (
                      <div key={f.name} className="flex items-center justify-between text-xs">
                        <span className="font-mono text-foreground truncate">{f.name}</span>
                        <span className="text-muted-foreground whitespace-nowrap">
                          <span className="text-emerald-600">+{f.additions}</span>{' '}
                          <span className="text-red-600">−{f.deletions}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">Could not load PR details.</p>
            )}

            {/* ── Approve / Reject ── */}
            {current.status === 'awaiting_approval' && (
              <div className="flex items-center gap-2 mt-5">
                <button
                  type="button"
                  onClick={() => onDecide(row, 'approved')}
                  className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
                >
                  <CheckCircle size={13} /> Approve & Merge
                </button>
                <button
                  type="button"
                  onClick={() => onDecide(row, 'rejected')}
                  className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90"
                >
                  <XCircle size={13} /> Reject & Close
                </button>
              </div>
            )}

            {/* ── Apply logs + cancel (running) / final logs (done) ──── */}
            {(jobRunning || (jobDone && complete)) && (
              <div className="mt-5 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-foreground">
                    {current.status === 'approved' ? 'Applying accepted drift…' : 'Reverting drift…'}
                  </p>
                  {jobRunning && (
                    <button
                      type="button"
                      onClick={cancel}
                      disabled={cancelling}
                      className="inline-flex items-center gap-1 rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
                    >
                      <Ban size={11} /> {cancelling ? 'Cancelling…' : 'Cancel'}
                    </button>
                  )}
                </div>
                <LogViewer lines={lines} complete={complete} />
              </div>
            )}
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Approvals() {
  const { scope } = useScope();
  const queryClient = useQueryClient();

  const [selected, setSelected] = useState<PendingApply | null>(null);

  const { data, isLoading, error, isFetching } = useQuery<PendingApply[]>({
    queryKey: ['pendingApplies', scope],
    enabled: !!scope,
    refetchInterval: 30_000,
    queryFn: () => apiFetch<PendingApply[]>(`/pending-applies?status=all${scope ? `&scope=${encodeURIComponent(scope)}` : ''}`),
  });

  const decide = useCallback(async (row: PendingApply, decision: 'approved' | 'rejected') => {
    try {
      const res = await apiFetch<{ apply_started?: boolean; error?: string }>(
        `/pending-applies/${row.id}/decision`,
        {
          method: 'POST',
          body: JSON.stringify({
            decision,
            // No per-user identity in this deployment — the token holder is the operator.
            approved_by: 'dashboard-user',
          }),
        },
      );
      if (res.apply_started === false) {
        // Defensive: the backend returns non-2xx here now, but don't
        // toast success for a job that never spawned if it ever slips
        // through as a 200.
        toast.error(decision === 'approved' ? 'Failed to approve' : 'Failed to reject', {
          description: res.error ?? 'Apply job did not start — no job was spawned.',
        });
        return;
      }
      toast.success(decision === 'approved'
        ? `PR #${row.pr_number} approved — merge + apply started`
        : `PR #${row.pr_number} rejected — close + revert started`);
      queryClient.invalidateQueries({ queryKey: ['pendingApplies'] });
      queryClient.invalidateQueries({ queryKey: ['pendingApplies', scope] });
      // Refetch the drawer's live row immediately — otherwise the log
      // poller waits for the 3s refetchInterval before it even starts.
      queryClient.invalidateQueries({ queryKey: ['pendingApply', row.id] });
    } catch (err) {
      // Backend surfaces merge/close failures as ApiError with a clear
      // message (e.g. "GitHub merge failed: 405 Branch protection...").
      toast.error(decision === 'approved' ? 'Failed to approve' : 'Failed to reject', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }, [queryClient, scope]);

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
        Click a PR to review its details, commits, checks, and file changes before approving or rejecting.
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
          <p className="text-xs text-muted-foreground/70">New drift-fix PRs appear here as soon as a scan creates them.</p>
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
                    <tr
                      key={row.id}
                      onClick={() => setSelected(row)}
                      className={[
                        'cursor-pointer transition-colors',
                        selected?.id === row.id ? 'bg-accent' : 'hover:bg-muted/50',
                      ].join(' ')}
                    >
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
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
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
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <DetailDrawer row={selected} onClose={() => setSelected(null)} onDecide={decide} />
    </div>
  );
}
