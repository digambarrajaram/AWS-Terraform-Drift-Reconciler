import { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { format, formatDistanceToNow } from 'date-fns';
import { toast } from 'sonner';
import {
  Play, RotateCcw, CheckCircle, XCircle, Clock,
  ExternalLink, ChevronRight, Loader2, FileText, Ban, Shield,
} from 'lucide-react';

import { Skeleton } from '@/components/ui/skeleton';
import { LogViewer } from '@/components/shared/LogViewer';
import { apiFetch, ApiError } from '@/api/apiFetch';
import { useScope } from '@/hooks/useScope';
import { useScanLogs } from '@/hooks/useScanLogs';
import {
  useScanRunHistory, useScanRun, useInvalidateScanRuns, type ScanRun,
} from '@/hooks/useScanRuns';

// ── Constants ──────────────────────────────────────────────────────────────

const STAGES = [
  { key: 'unmanaged_scan',  label: 'Unmanaged Scan'  },
  { key: 'reconcile_agent', label: 'Reconcile'        },
  { key: 'trivy_gate',      label: 'Trivy Gate'       },
  { key: 'alert_agent',     label: 'Alert Agent'      },
  { key: 'drift_pr',        label: 'Drift PR'         },
] as const;

const TRIVY_ONLY_STAGES = [
  { key: 'trivy_only_scan', label: 'Security Scan' },
  { key: 'trivy_only_pr',   label: 'Security PR'    },
] as const;

const UNMANAGED_ONLY_STAGES = [
  { key: 'unmanaged_scan', label: 'Unmanaged Scan' },
  { key: 'drift_pr',       label: 'Unmanaged PR'   },
] as const;

const DRIFT_ONLY_STAGES = [
  { key: 'reconcile_agent', label: 'Reconcile'   },
  { key: 'trivy_gate',      label: 'Trivy Gate'  },
  { key: 'alert_agent',     label: 'Alert Agent' },
  { key: 'drift_pr',        label: 'Drift PR'    },
] as const;

// ── Helpers ────────────────────────────────────────────────────────────────

function relTime(iso: string) {
  try { return formatDistanceToNow(new Date(iso), { addSuffix: true }); }
  catch { return '—'; }
}

function fmtDate(iso: string) {
  try { return format(new Date(iso), 'MMM d, yyyy, HH:mm'); }
  catch { return '—'; }
}

function stageLabel(run: ScanRun): string {
  if (!run.current_stage) return '—';
  const type = run.scan_type ?? run.result_summary?.mode;
  const set = type === 'trivy_only' ? TRIVY_ONLY_STAGES
    : type === 'unmanaged_only' ? UNMANAGED_ONLY_STAGES
    : type === 'drift_only' ? DRIFT_ONLY_STAGES
    : STAGES;
  const match = set.find(s => s.key === run.current_stage);
  return match?.label ?? run.current_stage;
}

// ── StageIndicator ─────────────────────────────────────────────────────────

function StageIndicator({ currentStage, status, stages = STAGES }: {
  currentStage: string | null;
  status: string;
  stages?: typeof STAGES | typeof TRIVY_ONLY_STAGES | typeof UNMANAGED_ONLY_STAGES | typeof DRIFT_ONLY_STAGES;
}) {
  // Complete → all stages done.  Failed → only stages before the failure
  // are green; the failing stage shows ✕ (not a success checkmark).
  const currentIdx = status === 'complete'
    ? stages.length
    : stages.findIndex((s) => s.key === currentStage);

  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-1">
      {stages.map((stage, i) => {
        const isPast    = currentIdx >= 0 && i < currentIdx;
        const isCurrent = stage.key === currentStage;
        const isFailed  = isCurrent && status === 'failed';

        return (
          <div key={stage.key} className="flex items-center gap-1 shrink-0">
            <div className="flex flex-col items-center gap-1">
              <div
                className={[
                  'flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold transition-colors',
                  isFailed
                    ? 'bg-destructive text-destructive-foreground'
                    : isCurrent
                      ? 'bg-primary text-primary-foreground ring-2 ring-primary/30'
                      : isPast
                        ? 'bg-emerald-500 text-white'
                        : 'bg-muted text-muted-foreground',
                ].join(' ')}
              >
                {isFailed ? '✕' : isPast ? '✓' : i + 1}
              </div>
              <span className={[
                'text-[10px] whitespace-nowrap',
                isFailed ? 'text-destructive font-medium'
                  : isCurrent ? 'text-foreground font-medium' : 'text-muted-foreground',
              ].join(' ')}>
                {stage.label}
              </span>
            </div>
            {i < stages.length - 1 && (
              <div className={[
                'mb-4 h-px w-6 shrink-0',
                isPast ? 'bg-emerald-500' : 'bg-border',
              ].join(' ')} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── ScanResult ─────────────────────────────────────────────────────────────

function ScanResult({ run }: { run: ScanRun }) {
  const { result_summary: rs, status, current_stage } = run;
  const failed = status === 'failed';
  const failSummary = typeof rs?.summary === 'string' ? rs.summary : null;
  const failDetail = typeof rs?.detail === 'string' ? rs.detail : null;
  const failSuggestion = typeof rs?.suggestion === 'string' ? rs.suggestion : null;

  return (
    <div className={[
      'rounded-xl border p-5 space-y-4',
      failed ? 'border-destructive/40 bg-destructive/5' : 'border-emerald-500/40 bg-emerald-500/5',
    ].join(' ')}>
      <div className="flex items-center gap-2">
        {failed
          ? <XCircle size={18} className="text-destructive" />
          : <CheckCircle size={18} className="text-emerald-500" />}
        <span className="font-semibold text-sm">
          {failed
            ? (failSummary ?? `Scan failed at ${current_stage ?? 'unknown stage'}`)
            : 'Scan complete'}
        </span>
        {run.completed_at && (
          <span className="ml-auto text-xs text-muted-foreground">
            {relTime(run.completed_at)}
          </span>
        )}
      </div>

      {failed && (failSuggestion || failDetail) && (
        <div className="space-y-2 text-sm">
          {failSuggestion && (
            <p className="text-foreground/90">{failSuggestion}</p>
          )}
          {failDetail && (
            <details className="rounded-md border border-border bg-card">
              <summary className="cursor-pointer px-3 py-1.5 text-[11px] font-mono text-muted-foreground select-none">
                technical details
              </summary>
              <pre className="whitespace-pre-wrap break-all px-3 pb-3 text-xs font-mono text-foreground/80 overflow-x-auto max-h-48">
                {failDetail}
              </pre>
            </details>
          )}
        </div>
      )}

      {rs && !failed && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {/* Drift */}
          <div className="rounded-lg border border-border bg-card p-3">
            <p className="text-xs font-medium text-muted-foreground mb-1">Drift</p>
            {rs.drift?.found ? (
              <>
                <p className="text-lg font-semibold text-card-foreground">{rs.drift.count} finding{rs.drift.count !== 1 ? 's' : ''}</p>
                {(rs.drift.pr_links ?? []).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {rs.drift.pr_links.map((url) => {
                      const prNum = url.split('/').pop();
                      return (
                        <a key={url} href={url} target="_blank" rel="noopener noreferrer"
                          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent no-underline">
                          <ExternalLink size={11} /> View PR #{prNum}
                        </a>
                      );
                    })}
                  </div>
                )}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">No drift found</p>
            )}
          </div>

          {/* Unmanaged */}
          <div className="rounded-lg border border-border bg-card p-3">
            <p className="text-xs font-medium text-muted-foreground mb-1">Unmanaged</p>
            {rs.unmanaged?.found ? (
              <>
                <p className="text-lg font-semibold text-card-foreground">{rs.unmanaged.count} finding{rs.unmanaged.count !== 1 ? 's' : ''}</p>
                {(rs.unmanaged.pr_links ?? []).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {rs.unmanaged.pr_links.map((url) => {
                      const prNum = url.split('/').pop();
                      return (
                        <a key={url} href={url} target="_blank" rel="noopener noreferrer"
                          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent no-underline">
                          <ExternalLink size={11} /> View PR #{prNum}
                        </a>
                      );
                    })}
                  </div>
                )}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">No unmanaged resources</p>
            )}
          </div>
        </div>
      )}

      {rs?.alerts_sent && !failed && (
        <div className="flex gap-4 text-xs text-muted-foreground">
          <span>PagerDuty alerts: <strong className="text-foreground">{rs.alerts_sent.pagerduty}</strong></span>
          <span>Slack alerts: <strong className="text-foreground">{rs.alerts_sent.slack}</strong></span>
        </div>
      )}

      {failed && !failDetail && (
        <p className="text-xs text-muted-foreground">
          See the log viewer above for details on the failure.
        </p>
      )}
    </div>
  );
}

// ── ScanHistory ────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, { label: string; cls: string }> = {
  running:   { label: 'Running',   cls: 'bg-amber-100  text-amber-700  dark:bg-amber-900/30 dark:text-amber-400'  },
  complete:  { label: 'Complete',  cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
  failed:    { label: 'Failed',    cls: 'bg-red-100    text-red-700    dark:bg-red-900/30 dark:text-red-400'      },
  cancelled: { label: 'Cancelled', cls: 'bg-slate-100  text-slate-700  dark:bg-slate-900/30 dark:text-slate-400' },
};

function ScanHistory({ runs, activeRunId, onSelect, loading }: {
  runs: ScanRun[];
  activeRunId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}) {
  return (
    <div>
      <h2 className="mb-3 text-sm font-semibold text-foreground">Scan History</h2>
      <div className="rounded-xl border border-border overflow-hidden">
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
          </div>
        ) : runs.length === 0 ? (
          <div className="p-6 text-center text-sm text-muted-foreground">No scan history yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40 text-left">
                <th className="px-4 py-2.5 text-xs font-medium text-muted-foreground">Started</th>
                <th className="px-4 py-2.5 text-xs font-medium text-muted-foreground">Status</th>
                <th className="px-4 py-2.5 text-xs font-medium text-muted-foreground">Stage</th>
                <th className="px-4 py-2.5 text-xs font-medium text-muted-foreground">Mode</th>
                <th className="px-4 py-2.5 text-xs font-medium text-muted-foreground">Drift</th>
                <th className="px-4 py-2.5 text-xs font-medium text-muted-foreground">Unmanaged</th>
                <th className="px-4 py-2.5 text-xs font-medium text-muted-foreground"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {runs.map((run) => {
                const style = STATUS_STYLE[run.status] ?? {
                  label: run.status,
                  cls: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
                };
                const isActive = run.id === activeRunId;
                return (
                  <tr
                    key={run.id}
                    onClick={() => onSelect(run.id)}
                    className={[
                      'cursor-pointer transition-colors',
                      isActive ? 'bg-accent' : 'hover:bg-muted/50',
                    ].join(' ')}
                  >
                    <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap">
                      {fmtDate(run.started_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${style.cls}`}>
                        {style.label}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground text-xs">
                      {stageLabel(run)}
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground text-xs">
                      {run.scan_type ?? run.result_summary?.mode ?? '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs">
                      {run.result_summary?.drift?.found
                        ? <span className="text-amber-600 dark:text-amber-400 font-medium">{run.result_summary.drift.count}</span>
                        : <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className="px-4 py-2.5 text-xs">
                      {run.result_summary?.unmanaged?.found
                        ? <span className="text-amber-600 dark:text-amber-400 font-medium">{run.result_summary.unmanaged.count}</span>
                        : <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      <ChevronRight size={14} className={isActive ? 'text-foreground' : ''} />
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

export default function Scan() {
  const { scope }   = useScope();
  const invalidate  = useInvalidateScanRuns();

  const [activeRunId,   setActiveRunId]   = useState<string | null>(null);
  const [scanMode,  setScanMode]  = useState('drift_only');
  const [submittingScan,     setSubmittingScan]     = useState(false);
  const [submittingSecurity, setSubmittingSecurity] = useState(false);
  const submitting = submittingScan || submittingSecurity;

  const history    = useScanRunHistory(scope);
  const activeRun  = useScanRun(activeRunId, scope);
  const { lines, complete: logsComplete } = useScanLogs(activeRunId);

  // When logs complete, refetch history + active run for final status
  useEffect(() => {
    if (logsComplete && activeRunId && scope) {
      invalidate(scope, activeRunId);
    }
  }, [logsComplete, activeRunId, scope, invalidate]);

  const handleCancel = useCallback(async () => {
    if (!activeRunId) return;
    try {
      await apiFetch(`/scan/${activeRunId}/cancel`, { method: 'POST' });
      toast.success('Scan cancelled');
    } catch (err) {
      toast.error('Failed to cancel scan', {
        description: err instanceof Error ? err.message : String(err),
      });
      return;
    }
    // Refetch scan history so the cancelled status appears without refresh.
    if (scope) invalidate(scope, activeRunId);
    setActiveRunId(null);
  }, [activeRunId, scope, invalidate]);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!scope) return;
    setSubmittingScan(true);
    try {
      const res = await apiFetch<{ run_id: string }>('/scan', {
        method: 'POST',
        body: JSON.stringify({ scope, scan_mode: scanMode }),
      });
      setActiveRunId(res.run_id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.runId) {
        toast.warning('Scan already running', {
          description: `Run ID: ${err.runId}`,
          action: { label: 'View', onClick: () => setActiveRunId(err.runId!) },
        });
        setActiveRunId(err.runId);
      } else {
        toast.error('Failed to start scan', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    } finally {
      setSubmittingScan(false);
    }
  }, [scope, scanMode]);

  const handleTrivyOnlyScan = useCallback(async () => {
    if (!scope) return;
    setSubmittingSecurity(true);
    try {
      const res = await apiFetch<{ run_id: string }>('/scan/trivy-only', {
        method: 'POST',
        body: JSON.stringify({ scope }),
      });
      setActiveRunId(res.run_id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.runId) {
        toast.warning('Scan already running', {
          description: `Run ID: ${err.runId}`,
          action: { label: 'View', onClick: () => setActiveRunId(err.runId!) },
        });
        setActiveRunId(err.runId);
      } else {
        toast.error('Failed to start security scan', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    } finally {
      setSubmittingSecurity(false);
    }
  }, [scope]);

  const run         = activeRun.data;
  const isRunning   = run?.status === 'running';
  const isDone      = run?.status === 'complete' || run?.status === 'failed' || run?.status === 'cancelled';
  const showResult  = isDone && run != null;
  const showLog     = activeRunId != null;

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Scan</h1>
        {activeRunId && (
          <button
            type="button"
            onClick={() => setActiveRunId(null)}
            className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <Play size={12} /> New Scan
          </button>
        )}
      </div>

      {/* ── Trigger area ───────────────────────────────────────────────── */}
      {!activeRunId ? (
        /* Idle form */
        <div className="rounded-xl border border-border bg-card p-5 space-y-4">
          <h2 className="text-sm font-semibold text-card-foreground">Start a Scan</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-[11px] font-medium text-muted-foreground">Scan Mode</label>
              <select
                value={scanMode}
                onChange={(e) => setScanMode(e.target.value)}
                className="rounded-md border border-input bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring w-full max-w-xs"
              >
                <option value="drift_only">Drift only</option>
                <option value="drift_and_unmanaged">Drift + Unmanaged</option>
                <option value="unmanaged_only">Unmanaged only</option>
              </select>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={submitting || !scope}
                className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {submittingScan
                  ? <><Loader2 size={14} className="animate-spin" /> Starting…</>
                  : <><Play size={14} /> Run Scan</>}
              </button>
              <button
                type="button"
                disabled={submitting || !scope}
                onClick={handleTrivyOnlyScan}
                className="flex items-center gap-2 rounded-md border border-border bg-card px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
              >
                {submittingSecurity
                  ? <><Loader2 size={14} className="animate-spin" /> Starting…</>
                  : <><Shield size={14} /> Security Scan</>}
              </button>
            </div>
          </form>
        </div>
      ) : (
        /* Active run view */
        <div className="rounded-xl border border-border bg-card p-5 space-y-4">
          {/* Header */}
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-2">
              {isRunning ? (
                <Loader2 size={15} className="animate-spin text-primary" />
              ) : isDone ? (
                run?.status === 'complete'
                  ? <CheckCircle size={15} className="text-emerald-500" />
                  : run?.status === 'cancelled'
                    ? <Ban size={15} className="text-slate-500" />
                    : <XCircle size={15} className="text-destructive" />
              ) : (
                <Clock size={15} className="text-muted-foreground" />
              )}
              <span className="text-sm font-semibold text-card-foreground">
                {isRunning ? 'Scan in progress' : isDone ? `Scan ${run!.status}` : 'Loading…'}
              </span>
            </div>
            <span className="text-xs text-muted-foreground font-mono">{activeRunId}</span>
          </div>

          {/* Cancel — only while running */}
          {isRunning && (
            <button
              type="button"
              onClick={handleCancel}
              className="flex items-center gap-1.5 rounded-md bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90"
            >
              <Ban size={12} /> Cancel Scan
            </button>
          )}

          {/* Stage indicator */}
          {run && (
            <StageIndicator
              currentStage={run.current_stage}
              status={run.status}
              stages={(() => {
                const type = run.scan_type ?? run.result_summary?.mode;
                switch (type) {
                  case 'trivy_only':         return TRIVY_ONLY_STAGES;
                  case 'unmanaged_only':     return UNMANAGED_ONLY_STAGES;
                  case 'drift_only':         return DRIFT_ONLY_STAGES;
                  case 'drift_and_unmanaged': return STAGES;
                  default:                    return STAGES;
                }
              })()}
            />
          )}

          {/* Log viewer */}
          {showLog && <LogViewer lines={lines} complete={logsComplete} />}

          {/* Result summary */}
          {showResult && <ScanResult run={run!} />}

          {/* Skeleton while run record loads */}
          {!run && activeRun.isLoading && (
            <Skeleton className="h-16 w-full" />
          )}
        </div>
      )}

      {/* ── History ────────────────────────────────────────────────────── */}
      <ScanHistory
        runs={history.data ?? []}
        activeRunId={activeRunId}
        onSelect={setActiveRunId}
        loading={history.isLoading}
      />
    </div>
  );
}
