import { useCallback, useEffect, useRef, useState } from 'react';
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
import { runningLabel, decisionButtonLabel, decisionToast, isJobDone } from './approval-labels';
import { useScanLogs } from '@/hooks/useScanLogs';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { errorMessage } from '@/lib/errorUtils';

// ── Types ──────────────────────────────────────────────────────────────────

interface PendingApply {
  id: string;
  pr_number: number;
  scope: string;
  status: 'awaiting_approval' | 'approved' | 'rejected' | 'excepted' | 'applied' | 'failed'
    | 'cancelled' | 'reverted' | 'reverted_gate_blocked' | 'manual_revert_required';
  pr_type: string | null;
  review_only?: boolean | null;
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

const PR_TYPE_LABEL: Record<string, string> = {
  fix: 'Fix', batch: 'Batch', unmanaged: 'Unmanaged',
  security_only: 'Security', rollback: 'Rollback', manual: 'Manual',
};

// approved/rejected are the backend's *claim* states — the job is still
// running, so they must never render as a terminal-looking status.  The
// list shows a running badge instead; the final status (applied/failed/
// …) is only displayed once the backend job writes it.
const RUNNING_STYLE = 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 animate-pulse';

function displayStatus(status: PendingApply['status'], prType: PendingApply['pr_type']): { label: string; style: string } {
  const running = runningLabel(status, prType);
  return running
    ? { label: running, style: RUNNING_STYLE }
    : { label: status.replace(/_/g, ' '), style: STATUS_STYLE[status] };
}

const STATUS_STYLE: Record<PendingApply['status'], string> = {
  awaiting_approval:      'bg-amber-100  text-amber-700  dark:bg-amber-900/30 dark:text-amber-400',
  approved:               'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  rejected:               'bg-red-100    text-red-700    dark:bg-red-900/30 dark:text-red-400',
  excepted:               'bg-amber-100  text-amber-800  dark:bg-amber-900/30 dark:text-amber-300',
  applied:                'bg-blue-100   text-blue-700   dark:bg-blue-900/30  dark:text-blue-400',
  reverted:               'bg-blue-100   text-blue-700   dark:bg-blue-900/30  dark:text-blue-400',
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
  row, onClose, onDecide, onCancel, deciding, cancelling,
}: {
  row: PendingApply | null;
  onClose: () => void;
  onDecide: (row: PendingApply, decision: 'approved' | 'rejected' | 'excepted') => void;
  onCancel: (row: PendingApply) => void;
  deciding: boolean;
  cancelling: boolean;
}) {
  const open = !!row;

  const { data: details, isLoading: detailsLoading } = useQuery<PrDetails>({
    queryKey: ['prDetails', row?.id],
    enabled: !!row,
    queryFn: () => apiFetch<PrDetails>(
      `/pending-applies/${row!.id}/pr-details?scope=${encodeURIComponent(row!.scope)}`,
    ),
  });

  // One poller per open drawer: the serialized log poll (useScanLogs)
  // doubles as the live-status feed — the endpoint returns the row's
  // status in the same page it computes `complete` from, so the badge
  // updates at poll cadence (800 ms) without a second /pending-applies/
  // {id} query running alongside.  The list query still refreshes rows
  // with no job (awaiting_approval polls nothing).
  //
  // Apply/revert job logs are keyed by the pending row id.  Keep the id
  // even after the status turns terminal (applied/failed/…) so the final
  // logs persist in the drawer — nulling it there would reset the hook's
  // accumulated lines exactly when "Job finished" is about to render.
  // Backend run state for the indicator:
  //   approved/rejected  → job spawned, still running (claim state)
  //   applied/failed/reverted/manual_revert_required/
  //   reverted_gate_blocked/excepted/cancelled → done (terminal;
  //   'reverted' = file-only reject; 'excepted' = sync Except, no spawn)
  // complete lags one render behind the hook's fetch — read it through a
  // ref so the active gate below can reference it without a circular
  // dependency on the hook call it feeds.
  const completeRef = useRef(false);
  // Sticky run id: once this drawer has left awaiting_approval, keep polling
  // that id even across status flips.  Nulling runId on a brief status blip
  // would clear useScanLogs' buffer exactly when "Job finished" renders.
  const logRunIdRef = useRef<string | null>(null);
  const openId = row?.id ?? null;
  const prevOpenIdRef = useRef<string | null>(null);
  if (!row) {
    logRunIdRef.current = null;
  } else if (row.status !== 'awaiting_approval') {
    logRunIdRef.current = row.id;
  }
  const logRunId = logRunIdRef.current;
  // Reset the gate before useScanLogs so a prior PR's complete:true doesn't
  // force active=false on the first render of the next row.  Don't mirror
  // ``complete`` on that same render — useScanLogs still returns the previous
  // run's flag until its reset effect runs.
  const openIdChanged = prevOpenIdRef.current !== openId;
  if (openIdChanged) {
    prevOpenIdRef.current = openId;
    completeRef.current = false;
  }

  // Live status comes from the log poll; the list row is the fallback for
  // rows that never had a job (awaiting_approval polls nothing).  Derive
  // jobRunning/jobDone from *current* — using the stale list `row.status`
  // while logStatus already flipped to applied caused "Job finished" and
  // the spinning Cancel banner to show together.
  const { lines, complete, status: logStatus } = useScanLogs(
    logRunId,
    !!(row && (
      ['approved', 'rejected'].includes(row.status)
      || (isJobDone(row.status) && !completeRef.current)
    )),
    'pending',
  );
  if (!openIdChanged) {
    completeRef.current = complete;
  }

  const current = row && !openIdChanged && logStatus
    ? { ...row, status: logStatus as PendingApply['status'] }
    : row;
  const jobRunning = !!(current && ['approved', 'rejected'].includes(current.status));
  const jobDone = !!(current && isJobDone(current.status));

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
                <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${displayStatus(current.status, current.pr_type).style}`}>
                  {displayStatus(current.status, current.pr_type).label}
                </span>
              </SheetTitle>
            </SheetHeader>

            {/* ── Job run indicator ── */}
            {jobRunning && !jobDone && (
              <div className="mb-4 flex items-center gap-2 rounded-lg border border-amber-400/40 bg-amber-100/50 dark:bg-amber-900/20 px-3 py-2">
                <Loader2 size={14} className="animate-spin text-amber-600 dark:text-amber-400" />
                <span className="text-xs text-amber-800 dark:text-amber-300">
                  {runningLabel(current.status, current.pr_type) || 'Working…'} — job in progress
                </span>
              </div>
            )}
            {jobDone && (
              <div className="mb-4 flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
                {current.status === 'applied' || current.status === 'excepted'
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

            {/* ── Accept AWS / Except / Revert AWS ── */}
            {/* Real-fix security: Accept (merge patch) + Except + Revert.
                Review-only security (no .tf patch): Except + Close only —
                Accept/Merge would only add exceptions, which is Except's job. */}
            {current.status === 'awaiting_approval' && (
              <div className="flex items-center gap-2 mt-5 flex-wrap">
                {!(current.pr_type === 'security_only' && current.review_only) && (
                  <button
                    type="button"
                    disabled={deciding}
                    onClick={() => onDecide(current, 'approved')}
                    className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    <CheckCircle size={13} /> {deciding ? 'Working…' : decisionButtonLabel('approved', current.pr_type)}
                  </button>
                )}
                {current.pr_type === 'security_only' && (
                  <button
                    type="button"
                    disabled={deciding}
                    onClick={() => onDecide(current, 'excepted')}
                    className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/60 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-800 dark:text-amber-300 transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    <ShieldCheck size={13} /> {deciding ? 'Working…' : decisionButtonLabel('excepted', current.pr_type)}
                  </button>
                )}
                <button
                  type="button"
                  disabled={deciding}
                  onClick={() => onDecide(current, 'rejected')}
                  className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  <XCircle size={13} /> {decisionButtonLabel('rejected', current.pr_type)}
                </button>
              </div>
            )}

            {/* ── Apply logs + cancel (running) / final logs (done) ──── */}
            {/* Show whenever we have lines or a job in flight/finished —
                do not wait for complete:true.  Gating on complete hid the
                panel in the gap between status=applied and the log poll's
                final page, which looked like the buffer was wiped. */}
            {(jobRunning || jobDone || lines.length > 0) && (
              <div className="mt-5 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-foreground">
                    {runningLabel(current.status, current.pr_type) || (complete ? 'Log complete' : jobDone ? 'Finishing…' : 'Working…')}
                  </p>
                  {jobRunning && (
                    <button
                      type="button"
                      onClick={() => onCancel(current)}
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
  const [deciding, setDeciding] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const { data, isLoading, error, isFetching } = useQuery<PendingApply[]>({
    queryKey: ['pendingApplies', scope],
    enabled: !!scope,
    // Poll fast while any row's job is still running (approved/rejected
    // claim state); slow back down once everything is terminal.  The
    // backend writes the final status (applied/failed/…) itself, so the
    // displayed status only flips when the job confirms.
    refetchInterval: (query) =>
      (query.state.data as PendingApply[] | undefined)?.some(
        (r) => r.status === 'approved' || r.status === 'rejected',
      )
        ? 3000
        : 30_000,
    queryFn: () => apiFetch<PendingApply[]>(`/pending-applies?status=all${scope ? `&scope=${encodeURIComponent(scope)}` : ''}`),
  });

  // Keep the open drawer in sync with list polls.  Without this, a
  // successful Approve leaves `selected.status` stuck on awaiting_approval
  // so the buttons stay clickable and a second POST 409s ("already handled").
  useEffect(() => {
    if (!selected || !data) return;
    const fresh = data.find((r) => r.id === selected.id);
    if (!fresh) return;
    if (
      fresh.status !== selected.status
      || fresh.approved_at !== selected.approved_at
      || fresh.merged_at !== selected.merged_at
    ) {
      setSelected(fresh);
    }
  }, [data, selected]);

  const decide = useCallback(async (row: PendingApply, decision: 'approved' | 'rejected' | 'excepted') => {
    if (deciding) return;
    setDeciding(true);
    try {
      const res = await apiFetch<{ apply_started?: boolean; error?: string }>(
        `/pending-applies/${row.id}/decision`,
        {
          method: 'POST',
          body: JSON.stringify({
            decision,
            // No per-user identity in this deployment — the token holder is the operator.
            approved_by: window.localStorage.getItem('drift_operator_id') || 'dashboard-user',
          }),
        },
      );
      if (res.apply_started === false && decision !== 'excepted') {
        // Defensive: the backend returns non-2xx here now, but don't
        // toast success for a job that never spawned if it ever slips
        // through as a 200.  Except is sync (apply_started:false by design).
        toast.error(
          decision === 'approved' ? 'Failed to approve' : 'Failed to reject', {
          description: res.error ?? 'Apply job did not start — no job was spawned.',
        });
        return;
      }
      toast.success(decisionToast(row.pr_number, decision, row.pr_type, row.review_only));
      // Show the running/terminal state instantly — the claim lands in the
      // backend before this toast, so mirror it in the cache *and* the open
      // drawer without waiting for the next poll.  Final apply status still
      // only comes from the backend for Merge/Reject jobs.
      const claimed = { ...row, status: decision as PendingApply['status'] };
      setSelected((prev) => (prev?.id === row.id ? claimed : prev));
      queryClient.setQueryData<PendingApply[]>(['pendingApplies', scope], (rows) =>
        rows?.map((r) => (r.id === row.id ? { ...r, status: decision } : r)) ?? rows,
      );
      queryClient.invalidateQueries({ queryKey: ['pendingApplies'] });
      queryClient.invalidateQueries({ queryKey: ['pendingApplies', scope] });
      // Refetch the drawer's live row immediately — otherwise the log
      // poller waits for the 3s refetchInterval before it even starts.
      queryClient.invalidateQueries({ queryKey: ['pendingApply', row.id] });
    } catch (err) {
      // Backend surfaces merge/close failures and already-handled claims
      // as ApiError with a clear message (e.g. "Already handled — …").
      const msg = err instanceof Error ? err.message : String(err);
      const already = /already handled/i.test(msg);
      toast.error(
        already
          ? 'Already handled'
          : (decision === 'approved' ? 'Failed to approve'
            : decision === 'excepted' ? 'Failed to except'
              : 'Failed to reject'),
        { description: msg },
      );
      queryClient.invalidateQueries({ queryKey: ['pendingApplies', scope] });
    } finally {
      setDeciding(false);
    }
  }, [queryClient, scope, deciding]);

  // Mirror Scan.tsx Cancel Scan: POST …/cancel → status=cancelled + kill
  // the spawned apply/revert subprocess registered under the pending id.
  const cancel = useCallback(async (row: PendingApply) => {
    if (cancelling) return;
    setCancelling(true);
    try {
      await apiFetch(`/pending-applies/${row.id}/cancel`, { method: 'POST' });
      toast.success(`PR #${row.pr_number} decision cancelled`);
      const cancelled = { ...row, status: 'cancelled' as const };
      setSelected((prev) => (prev?.id === row.id ? cancelled : prev));
      queryClient.setQueryData<PendingApply[]>(['pendingApplies', scope], (rows) =>
        rows?.map((r) => (r.id === row.id ? cancelled : r)) ?? rows,
      );
      queryClient.invalidateQueries({ queryKey: ['pendingApplies', scope] });
    } catch (err) {
      toast.error('Failed to cancel', {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setCancelling(false);
    }
  }, [cancelling, queryClient, scope]);

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
        Click a PR to review details. Accept AWS changes updates code and state to match AWS;
        Revert AWS to code restores original Terraform on AWS.
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
                  {['PR', 'Scope', 'Type', 'Merged', 'Status', 'Decided By', 'Decided At', ''].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-xs font-medium text-muted-foreground whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => {
                  const shown = displayStatus(row.status, row.pr_type);
                  const awaiting = row.status === 'awaiting_approval';
                  const claimRunning = row.status === 'approved' || row.status === 'rejected';
                  const showApprove = !(row.pr_type === 'security_only' && row.review_only);
                  const showExcept = row.pr_type === 'security_only';
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
                        {row.pr_type === 'security_only' && row.review_only
                          ? 'Security (review)'
                          : row.pr_type
                            ? (PR_TYPE_LABEL[row.pr_type] ?? row.pr_type)
                            : '—'}
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
                        {fmtDate(row.merged_at)}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${shown.style}`}>
                          {shown.label}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">{row.approved_by ?? '—'}</td>
                      <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
                        {fmtDate(row.approved_at)}
                      </td>
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        {awaiting && (
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {showApprove && (
                              <button
                                type="button"
                                disabled={deciding}
                                onClick={() => decide(row, 'approved')}
                                className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                              >
                                <CheckCircle size={12} /> {decisionButtonLabel('approved', row.pr_type, { compact: true })}
                              </button>
                            )}
                            {showExcept && (
                              <button
                                type="button"
                                disabled={deciding}
                                onClick={() => decide(row, 'excepted')}
                                className="inline-flex items-center gap-1 rounded-md border border-amber-500/60 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-800 dark:text-amber-300 transition-opacity hover:opacity-90 disabled:opacity-50"
                              >
                                <ShieldCheck size={12} /> {decisionButtonLabel('excepted', row.pr_type, { compact: true })}
                              </button>
                            )}
                            <button
                              type="button"
                              disabled={deciding}
                              onClick={() => decide(row, 'rejected')}
                              className="inline-flex items-center gap-1 rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
                            >
                              <XCircle size={12} /> {decisionButtonLabel('rejected', row.pr_type, { compact: true })}
                            </button>
                          </div>
                        )}
                        {claimRunning && (
                          <button
                            type="button"
                            disabled={cancelling}
                            onClick={() => cancel(row)}
                            className="inline-flex items-center gap-1 rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
                          >
                            <Ban size={12} /> {cancelling ? 'Cancelling…' : 'Cancel'}
                          </button>
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

      <DetailDrawer
        row={selected}
        onClose={() => setSelected(null)}
        onDecide={decide}
        onCancel={cancel}
        deciding={deciding}
        cancelling={cancelling}
      />
    </div>
  );
}
