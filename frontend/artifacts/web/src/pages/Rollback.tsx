import { useState, useEffect, useCallback } from 'react';
import { format, formatDistanceToNow } from 'date-fns';
import { toast } from 'sonner';
import {
  RotateCcw, CheckCircle, XCircle, Loader2, ExternalLink,
  AlertTriangle, ChevronRight, Inbox,
} from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';

import { Skeleton } from '@/components/ui/skeleton';
import { LogViewer } from '@/components/shared/LogViewer';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

import { apiFetch, ApiError } from '@/api/apiFetch';
import { useScope } from '@/hooks/useScope';
import { useScanLogs } from '@/hooks/useScanLogs';
import {
  useEligiblePRs, useRollbackRun, useRollbackHistory,
  type RollbackRun, type PreviewDiffRow,
} from '@/hooks/useRollbackData';
import type { DriftEvent } from '@/types';

// ── Types ──────────────────────────────────────────────────────────────────

type Phase =
  | 'idle'
  | 'preview_running'
  | 'preview_done'
  | 'execute_running'
  | 'execute_done';

interface ActiveCtx {
  prNumber:       number;
  resourceId:     string;
  previewRunId:   string | null;
  executeRunId:   string | null;
  diff:           PreviewDiffRow[] | null;
  rollbackPrUrl:  string | null;
  failed:         boolean;
  failedPhase:    'preview' | 'execute' | null;
  // Human-readable error from the backend (humanize_rollback_error shape)
  errorSummary:   string | null;
  errorDetail:    string | null;
  errorHint:      string | null;
}

// ── Helpers ────────────────────────────────────────────────────────────────

const SEV: Record<string, string> = {
  HIGH:   'bg-red-100   text-red-700   dark:bg-red-900/30   dark:text-red-400',
  MEDIUM: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  LOW:    'bg-blue-100  text-blue-700  dark:bg-blue-900/30  dark:text-blue-400',
};

function SevBadge({ sev }: { sev: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${SEV[sev] ?? 'bg-muted text-muted-foreground'}`}>
      {sev}
    </span>
  );
}

function relTime(iso: string) {
  try { return formatDistanceToNow(new Date(iso), { addSuffix: true }); }
  catch { return '—'; }
}

function fmtDate(iso: string) {
  try { return format(new Date(iso), 'MMM d, HH:mm'); }
  catch { return '—'; }
}

function jsonEq(a: unknown, b: unknown) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function renderVal(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

// ── EligiblePRList ─────────────────────────────────────────────────────────

function EligiblePRList({
  events, loading, onPreview, activePrNumber,
}: {
  events: DriftEvent[];
  loading: boolean;
  onPreview: (e: DriftEvent) => void;
  activePrNumber: number | null;
}) {
  return (
    <div>
      <h2 className="mb-3 text-sm font-semibold">Eligible Open PRs</h2>
      <div className="rounded-xl border border-border overflow-hidden">
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : events.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <Inbox size={32} className="text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No open drift events with PRs</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Resource</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">PR #</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Severity</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Created</th>
                <th className="px-4 py-2.5 text-right text-xs font-medium text-muted-foreground"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {events.map((ev) => {
                // A baseline exists when the drift event has recorded the
                // before/after field values — without them there's nothing
                // to reverse.
                const hasBaseline = !!ev.changes_jsonb && Object.keys(ev.changes_jsonb).length > 0;
                return (
                <tr
                  key={ev.id}
                  className={[
                    'transition-colors',
                    activePrNumber === ev.pr_number ? 'bg-accent' : '',
                  ].join(' ')}
                >
                  <td className="px-4 py-3 font-mono text-xs max-w-[240px]">
                    <span className="block truncate" title={ev.resource_id}>{ev.resource_id}</span>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    #{ev.pr_number}
                  </td>
                  <td className="px-4 py-3">
                    <SevBadge sev={ev.severity} />
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap"
                      title={format(new Date(ev.created_at), 'PPpp')}>
                    {relTime(ev.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {hasBaseline ? (
                      <button
                        type="button"
                        onClick={() => onPreview(ev)}
                        disabled={activePrNumber === ev.pr_number}
                        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-40 disabled:pointer-events-none"
                      >
                        <RotateCcw size={11} />
                        Preview Rollback
                      </button>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground/60 italic"
                        title="This drift event was recorded before baseline tracking was enabled — no before/after field values are available to reverse."
                      >
                        No baseline available
                      </span>
                    )}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── DiffTable ──────────────────────────────────────────────────────────────

function DiffTable({ diff }: { diff: PreviewDiffRow[] }) {
  if (diff.length === 0) {
    return (
      <div className="rounded-xl border border-border py-10 text-center">
        <p className="text-sm text-muted-foreground">No changes in preview diff</p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-border overflow-hidden overflow-x-auto">
      <table className="w-full text-xs min-w-[640px]">
        <thead>
          <tr className="border-b border-border bg-muted/40">
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Resource</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Field</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Original</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Fixed</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Current Live</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">State</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {diff.map((row, i) => {
            const stale = !jsonEq(row.current_live, row.fixed);
            return (
              <tr key={`${row.resource_id}-${row.field}`} className={stale ? 'bg-amber-50/40 dark:bg-amber-900/10' : ''}>
                <td className="px-4 py-2.5 font-mono max-w-[160px]">
                  <span className="block truncate" title={row.resource_id}>{row.resource_id}</span>
                </td>
                <td className="px-4 py-2.5 font-mono text-foreground">{row.field}</td>
                <td className="px-4 py-2.5 font-mono text-muted-foreground max-w-[140px]">
                  <span className="block truncate" title={renderVal(row.original)}>{renderVal(row.original)}</span>
                </td>
                <td className="px-4 py-2.5 font-mono text-emerald-700 dark:text-emerald-400 max-w-[140px]">
                  <span className="block truncate" title={renderVal(row.fixed)}>{renderVal(row.fixed)}</span>
                </td>
                <td className={[
                  'px-4 py-2.5 font-mono max-w-[140px]',
                  stale ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground',
                ].join(' ')}>
                  <span className="block truncate" title={renderVal(row.current_live)}>{renderVal(row.current_live)}</span>
                </td>
                <td className="px-4 py-2.5 whitespace-nowrap">
                  {stale ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                      <AlertTriangle size={9} /> stale
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
                      <CheckCircle size={9} /> current
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── RollbackHistory ────────────────────────────────────────────────────────

const RUN_STATUS: Record<RollbackRun['status'], { label: string; cls: string }> = {
  running:  { label: 'Running',  cls: 'bg-amber-100  text-amber-700  dark:bg-amber-900/30 dark:text-amber-400'  },
  complete: { label: 'Complete', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
  failed:   { label: 'Failed',   cls: 'bg-red-100    text-red-700    dark:bg-red-900/30 dark:text-red-400'      },
};

function RollbackHistory({
  runs, loading, modeFilter, onModeFilter,
}: {
  runs: RollbackRun[];
  loading: boolean;
  modeFilter: string;
  onModeFilter: (v: string) => void;
}) {
  const filtered = modeFilter === 'all' ? runs : runs.filter((r) => r.mode === modeFilter);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-2 flex-wrap">
        <h2 className="text-sm font-semibold">Rollback History</h2>
        <select
          value={modeFilter}
          onChange={(e) => onModeFilter(e.target.value)}
          className="rounded-md border border-input bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="all">All modes</option>
          <option value="preview">Preview only</option>
          <option value="execute">Execute only</option>
        </select>
      </div>
      <div className="rounded-xl border border-border overflow-hidden">
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
          </div>
        ) : filtered.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted-foreground">No rollback history yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">PR #</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Mode</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Stage</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Started</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Completed</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map((run) => {
                const ss = RUN_STATUS[run.status];
                const prUrl = run.rollback_pr_url ?? run.result?.pr_url ?? null;
                const diffCount = run.result?.diff?.length;
                return (
                  <tr key={run.id} className="hover:bg-muted/40 transition-colors">
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">#{run.pr_number}</td>
                    <td className="px-4 py-2.5 text-xs capitalize text-foreground">{run.mode}</td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${ss.cls}`}>
                        {ss.label}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">{run.current_stage ?? '—'}</td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap">{fmtDate(run.started_at)}</td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
                      {run.completed_at ? relTime(run.completed_at) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs">
                      {run.mode === 'execute' && prUrl ? (
                        <a
                          href={prUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-primary hover:underline"
                        >
                          <ExternalLink size={11} /> View PR
                        </a>
                      ) : run.mode === 'preview' && diffCount != null ? (
                        <span className="text-muted-foreground">{diffCount} change{diffCount !== 1 ? 's' : ''}</span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Rollback() {
  const { scope }      = useScope();
  const queryClient    = useQueryClient();

  const [phase,          setPhase]         = useState<Phase>('idle');
  const [ctx,            setCtx]           = useState<ActiveCtx | null>(null);
  const [confirmOpen,    setConfirmOpen]   = useState(false);
  const [submitting,     setSubmitting]    = useState(false);
  const [histModeFilter, setHistModeFilter]= useState<string>('all');

  const eligible = useEligiblePRs(scope);
  const history  = useRollbackHistory(scope);

  const previewRun = useRollbackRun(ctx?.previewRunId ?? null);
  const executeRun = useRollbackRun(ctx?.executeRunId ?? null);

  const previewLogs = useScanLogs(ctx?.previewRunId ?? null);
  const executeLogs = useScanLogs(ctx?.executeRunId ?? null);

  // ── Invalidate run record when logs finish ────────────────────────────

  useEffect(() => {
    if (previewLogs.complete && ctx?.previewRunId) {
      queryClient.invalidateQueries({ queryKey: ['rollbackRun', ctx.previewRunId] });
    }
  }, [previewLogs.complete, ctx?.previewRunId, queryClient]);

  useEffect(() => {
    if (executeLogs.complete && ctx?.executeRunId) {
      queryClient.invalidateQueries({ queryKey: ['rollbackRun', ctx.executeRunId] });
    }
  }, [executeLogs.complete, ctx?.executeRunId, queryClient]);

  // ── Phase transitions on run completion ──────────────────────────────

  useEffect(() => {
    if (phase !== 'preview_running') return;
    const run = previewRun.data;
    if (!run) return;
    if (run.status === 'complete') {
      setCtx((prev) => prev ? { ...prev, diff: run.result?.diff ?? [] } : prev);
      setPhase('preview_done');
    } else if (run.status === 'failed') {
      setCtx((prev) => prev ? {
        ...prev, failed: true, failedPhase: 'preview',
        errorSummary: run.result?.summary ?? null,
        errorDetail:  run.result?.detail  ?? null,
        errorHint:    run.result?.suggestion ?? null,
      } : prev);
      setPhase('preview_done');
    }
  }, [phase, previewRun.data]);

  useEffect(() => {
    if (phase !== 'execute_running') return;
    const run = executeRun.data;
    if (!run) return;
    if (run.status === 'complete') {
      const prUrl = run.rollback_pr_url ?? run.result?.pr_url ?? null;
      setCtx((prev) => prev ? { ...prev, rollbackPrUrl: prUrl } : prev);
      setPhase('execute_done');
      toast.success('Rollback complete', {
        description: prUrl ? 'Reversing PR created successfully.' : undefined,
      });
      queryClient.invalidateQueries({ queryKey: ['rollbackHistory', scope] });
      queryClient.invalidateQueries({ queryKey: ['eligiblePRs', scope] });
    } else if (run.status === 'failed') {
      setCtx((prev) => prev ? {
        ...prev, failed: true, failedPhase: 'execute',
        errorSummary: run.result?.summary ?? null,
        errorDetail:  run.result?.detail  ?? null,
        errorHint:    run.result?.suggestion ?? null,
      } : prev);
      setPhase('execute_done');
      toast.error(run.result?.summary ?? 'Rollback execution failed', {
        description: 'Check the log above for technical details.',
      });
    }
  }, [phase, executeRun.data, scope, queryClient]);

  // ── Handlers ─────────────────────────────────────────────────────────

  const handlePreview = useCallback(async (ev: DriftEvent) => {
    if (!scope || !ev.pr_number) return;
    setCtx({
      prNumber:      ev.pr_number,
      resourceId:    ev.resource_id,
      previewRunId:  null,
      executeRunId:  null,
      diff:          null,
      rollbackPrUrl: null,
      failed:        false,
      failedPhase:   null,
      errorSummary:  null,
      errorDetail:   null,
      errorHint:     null,
    });
    setPhase('preview_running');
    try {
      const res = await apiFetch<{ run_id: string }>('/rollback/preview', {
        method: 'POST',
        body:   JSON.stringify({ pr_number: ev.pr_number, scope }),
      });
      setCtx((prev) => prev ? { ...prev, previewRunId: res.run_id } : prev);
    } catch (err) {
      toast.error('Failed to start preview', {
        description: err instanceof Error ? err.message : String(err),
      });
      setPhase('idle');
      setCtx(null);
    }
  }, [scope]);

  const handleExecute = useCallback(async () => {
    if (!scope || !ctx) return;
    setConfirmOpen(false);
    setSubmitting(true);
    setPhase('execute_running');
    try {
      const res = await apiFetch<{ run_id: string }>('/rollback/execute', {
        method: 'POST',
        body:   JSON.stringify({ pr_number: ctx.prNumber, scope }),
      });
      setCtx((prev) => prev ? { ...prev, executeRunId: res.run_id } : prev);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.runId) {
        toast.warning(`Rollback already running for PR #${ctx.prNumber}`, {
          description: `Run ID: ${err.runId}`,
          action: {
            label: 'View logs',
            onClick: () => {
              setCtx((prev) => prev ? { ...prev, executeRunId: err.runId! } : prev);
              setPhase('execute_running');
            },
          },
        });
        setCtx((prev) => prev ? { ...prev, executeRunId: err.runId! } : prev);
      } else {
        toast.error('Failed to execute rollback', {
          description: err instanceof Error ? err.message : String(err),
        });
        setPhase('preview_done'); // drop back to preview result
      }
    } finally {
      setSubmitting(false);
    }
  }, [scope, ctx]);

  function reset() {
    setPhase('idle');
    setCtx(null);
  }

  // ── Derived ───────────────────────────────────────────────────────────

  const activeLog      = phase === 'execute_running' ? executeLogs : previewLogs;
  const activeRunId    = phase === 'execute_running' ? ctx?.executeRunId : ctx?.previewRunId;
  const isRunning      = phase === 'preview_running' || phase === 'execute_running';
  const showLogPanel   = phase === 'preview_running' || phase === 'execute_running' ||
                         (phase === 'preview_done' && !!ctx?.previewRunId) ||
                         (phase === 'execute_done' && !!ctx?.executeRunId);

  const staleCount = ctx?.diff?.filter((r) => !jsonEq(r.current_live, r.fixed)).length ?? 0;

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Rollback</h1>
        {phase !== 'idle' && (
          <button
            type="button"
            onClick={reset}
            className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <RotateCcw size={12} /> Back to list
          </button>
        )}
      </div>

      {/* ── Active run panel ─────────────────────────────────────────────── */}
      {phase !== 'idle' && ctx && (
        <div className="rounded-xl border border-border bg-card p-5 space-y-4">
          {/* Panel header */}
          <div className="flex items-center gap-2 flex-wrap">
            {isRunning
              ? <Loader2 size={15} className="animate-spin text-primary" />
              : ctx.failed
                ? <XCircle size={15} className="text-destructive" />
                : <CheckCircle size={15} className="text-emerald-500" />}
            <span className="text-sm font-semibold text-card-foreground">
              {phase === 'preview_running' && `Previewing rollback for PR #${ctx.prNumber}…`}
              {phase === 'preview_done'    && (ctx.failed ? `Preview failed for PR #${ctx.prNumber}` : `Preview complete — PR #${ctx.prNumber}`)}
              {phase === 'execute_running' && `Executing rollback for PR #${ctx.prNumber}…`}
              {phase === 'execute_done'    && (ctx.failed ? `Execution failed for PR #${ctx.prNumber}` : `Rollback complete — PR #${ctx.prNumber}`)}
            </span>
            {activeRunId && (
              <span className="ml-auto text-[11px] font-mono text-muted-foreground">{activeRunId}</span>
            )}
          </div>

          {/* Log viewer — always show while running; keep visible after for reference */}
          {showLogPanel && activeRunId && (
            <LogViewer lines={activeLog.lines} complete={activeLog.complete} />
          )}

          {/* Preview diff */}
          {phase === 'preview_done' && !ctx.failed && ctx.diff != null && (
            <div className="space-y-3">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">
                    {ctx.diff.length} change{ctx.diff.length !== 1 ? 's' : ''}
                  </span>
                  {staleCount > 0 && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                      <AlertTriangle size={11} /> {staleCount} stale
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setConfirmOpen(true)}
                  disabled={submitting}
                  className="flex items-center gap-1.5 rounded-md bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  <RotateCcw size={12} /> Execute Rollback
                </button>
              </div>
              <DiffTable diff={ctx.diff} />
              {staleCount > 0 && (
                <p className="text-xs text-amber-700 dark:text-amber-400">
                  <AlertTriangle size={11} className="inline mr-1" />
                  {staleCount} field{staleCount !== 1 ? 's have' : ' has'} drifted further since this PR was raised.
                  Executing will apply the <em>fixed</em> value regardless.
                </p>
              )}
            </div>
          )}

          {/* Execute success */}
          {phase === 'execute_done' && !ctx.failed && (
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4 flex items-center justify-between gap-4 flex-wrap">
              <div className="flex items-center gap-2">
                <CheckCircle size={16} className="text-emerald-500 shrink-0" />
                <span className="text-sm font-medium">Reversing PR created</span>
              </div>
              {ctx.rollbackPrUrl && (
                <a
                  href={ctx.rollbackPrUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent"
                >
                  <ExternalLink size={11} /> View Rollback PR
                </a>
              )}
            </div>
          )}

          {/* Preview / Execute failed — show friendly summary when available */}
          {ctx.failed && ctx.errorSummary && (
            <div className="space-y-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4">
              <div className="flex items-start gap-2">
                <XCircle size={15} className="text-destructive shrink-0 mt-0.5" />
                <div className="space-y-1.5 min-w-0">
                  <p className="text-sm font-medium text-foreground">{ctx.errorSummary}</p>
                  {ctx.errorHint && (
                    <p className="text-xs text-muted-foreground">{ctx.errorHint}</p>
                  )}
                  {ctx.errorDetail && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground hover:text-foreground transition-colors">
                        Technical details
                      </summary>
                      <pre className="mt-2 max-h-40 overflow-y-auto rounded-md bg-muted p-3 text-[11px] font-mono text-muted-foreground whitespace-pre-wrap break-all">
                        {ctx.errorDetail}
                      </pre>
                    </details>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Fallback when no humanized error is available (legacy rows) */}
          {ctx.failed && !ctx.errorSummary && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 flex items-center gap-2">
              <XCircle size={15} className="text-destructive shrink-0" />
              <span className="text-sm text-muted-foreground">
                {ctx.failedPhase === 'preview'
                  ? 'Preview failed. Review the log above.'
                  : 'Execution failed. Review the log above.'}
              </span>
            </div>
          )}
        </div>
      )}

      {/* ── Eligible PRs list ─────────────────────────────────────────────── */}
      {phase === 'idle' && (
        <EligiblePRList
          events={eligible.data ?? []}
          loading={eligible.isLoading}
          onPreview={handlePreview}
          activePrNumber={ctx?.prNumber ?? null}
        />
      )}

      {/* ── History ─────────────────────────────────────────────────────── */}
      <RollbackHistory
        runs={history.data ?? []}
        loading={history.isLoading}
        modeFilter={histModeFilter}
        onModeFilter={setHistModeFilter}
      />

      {/* ── Confirm dialog ───────────────────────────────────────────────── */}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Execute rollback for PR #{ctx?.prNumber}?</AlertDialogTitle>
            <AlertDialogDescription>
              This will create a new reversing PR on GitHub that patches the infrastructure
              back to the pre-drift state. This action cannot be undone from this UI.
              {staleCount > 0 && (
                <span className="mt-2 block text-amber-700 dark:text-amber-400">
                  ⚠ {staleCount} field{staleCount !== 1 ? 's have' : ' has'} drifted further —
                  the rollback will apply the <em>fixed</em> snapshot regardless of current live state.
                </span>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleExecute}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Yes, execute rollback
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
